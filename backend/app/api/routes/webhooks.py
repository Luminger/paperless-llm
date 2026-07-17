"""Webhook ingress: paperless-ngx workflows POST here when documents
are added. Machine-to-machine auth via shared secret header (the
endpoint is disabled entirely when no secret is configured)."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AgentKind, EntityType, Session, StepKind
from app.db.session import get_session
from app.services.audit import record
from app.services.steps import create_step

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _extract_document_ids(body: Any) -> list[int]:
    """Tolerant extraction: paperless workflow webhook bodies are
    user-templated; accept the obvious shapes."""
    if isinstance(body, dict):
        for key in ("document_id", "id", "doc_id"):
            if isinstance(body.get(key), int):
                return [body[key]]
            if isinstance(body.get(key), str) and body[key].isdigit():
                return [int(body[key])]
        if isinstance(body.get("documents"), list):
            return [int(x) for x in body["documents"] if str(x).isdigit()]
        # e.g. {"url": "https://paperless/documents/123/"}
        for key in ("url", "doc_url"):
            if isinstance(body.get(key), str):
                m = re.search(r"/documents/(\d+)", body[key])
                if m:
                    return [int(m.group(1))]
    return []


@router.post("/paperless", status_code=202)
async def paperless_webhook(
    request: Request, db: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    cfg = get_settings().webhook
    if not cfg.secret:
        raise HTTPException(404, "webhook ingress not configured")
    if request.headers.get("X-PLLM-Token") != cfg.secret:
        raise HTTPException(403, "bad webhook token")

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — tolerate non-JSON bodies
        body = {}
    ids = _extract_document_ids(body)
    if not ids:
        raise HTTPException(422, "no document id found in webhook body")

    session_ids: list[int] = []
    for doc_id in ids:
        s = Session(
            agent_kind=AgentKind.document,
            entity_type=EntityType.document,
            entity_id=doc_id,
            params={
                "redo_ocr": cfg.redo_ocr,
                "apply_policy": cfg.apply_policy,
                "trigger": "webhook",
            },
            title=f"Document #{doc_id} analysis",
        )
        db.add(s)
        await db.flush()
        await create_step(
            db, s, StepKind.ocr if cfg.redo_ocr else StepKind.analysis
        )
        session_ids.append(s.id)
    await record(db, "webhook", "ingested", documents=ids, session_ids=session_ids)
    await db.commit()
    log.info("webhook queued sessions %s for documents %s", session_ids, ids)
    return {"queued_documents": ids, "session_ids": session_ids}
