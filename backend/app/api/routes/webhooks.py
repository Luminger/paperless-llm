"""Webhook ingress: paperless-ngx workflows POST here when documents
are added. Machine-to-machine auth via shared secret header (the
endpoint is disabled entirely when no secret is configured)."""

from __future__ import annotations

import hmac
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_paperless
from app.config import get_settings
from app.db.session import get_session
from app.paperless import PaperlessClient
from app.services.audit import record
from app.services.jobs import create_job

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _ascii_int(v: Any) -> int | None:
    """int() only for ASCII digits — str.isdigit() is True for unicode
    digits like "²" where int() raises (AUDIT API-F11)."""
    s = str(v)
    return int(s) if s.isascii() and s.isdigit() else None


def _extract_document_ids(body: Any) -> list[int]:
    """Tolerant extraction: paperless workflow webhook bodies are
    user-templated; accept the obvious shapes."""
    if isinstance(body, dict):
        for key in ("document_id", "id", "doc_id"):
            if isinstance(body.get(key), int):
                return [body[key]]
            if (n := _ascii_int(body.get(key))) is not None and isinstance(
                body.get(key), str
            ):
                return [n]
        if isinstance(body.get("documents"), list):
            return [n for x in body["documents"] if (n := _ascii_int(x)) is not None]
        # e.g. {"url": "https://paperless/documents/123/"}
        for key in ("url", "doc_url"):
            if isinstance(body.get(key), str):
                m = re.search(r"/documents/(\d+)", body[key])
                if m:
                    return [int(m.group(1))]
    return []


@router.post("/paperless", status_code=202)
async def paperless_webhook(
    request: Request,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> dict[str, Any]:
    cfg = get_settings().webhook
    if not cfg.secret:
        raise HTTPException(404, "webhook ingress not configured")
    # Constant-time compare (AUDIT API-F11) on BYTES: the str overload
    # raises TypeError on non-ASCII input — a garbage header must be a
    # 403, not a 500 (reinspection).
    supplied = request.headers.get("X-PLLM-Token", "").encode("utf-8", "replace")
    if not hmac.compare_digest(supplied, cfg.secret.encode("utf-8", "replace")):
        raise HTTPException(403, "bad webhook token")
    # Machine-to-machine: not a user action.
    from app.services.actor import actor_var

    actor_var.set("system")

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — tolerate non-JSON bodies
        body = {}
    ids = _extract_document_ids(body)
    if not ids:
        raise HTTPException(422, "no document id found in webhook body")

    # Webhook ingests are tracked jobs like every other analysis.
    job, queued = await create_job(
        db,
        paperless,
        document_ids=ids,
        redo_ocr=cfg.redo_ocr,
        apply_policy=cfg.apply_policy,
        kind="webhook_analyze",
        trigger="webhook",
    )
    from sqlalchemy import select

    from app.db.models import Session

    session_ids = list(
        (
            await db.scalars(
                select(Session.id).where(Session.job_id == job.id).order_by(Session.id)
            )
        ).all()
    )
    await record(
        db, "webhook", "ingested",
        documents=ids, session_ids=session_ids, job_id=job.id,
    )
    await db.commit()
    log.info("webhook queued sessions %s for documents %s", session_ids, ids)
    return {"queued_documents": ids, "session_ids": session_ids, "job_id": job.id}
