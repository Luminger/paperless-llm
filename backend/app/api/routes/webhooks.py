"""Webhook ingress: paperless-ngx workflows POST here when documents
are added. Machine-to-machine auth via shared secret header (the
endpoint is disabled entirely when no secret is configured).

Ingress caps (hardening review): bodies over ``webhook.max_body_bytes``
are 413, more than ``webhook.max_documents`` ids are 422, and ids
accepted within ``webhook.dedup_window_seconds`` are acknowledged but
not re-enqueued (replay brake)."""

from __future__ import annotations

import hmac
import json
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_paperless
from app.config import get_settings
from app.db.models import Session
from app.db.session import get_session
from app.paperless import PaperlessClient
from app.services.actor import actor_var
from app.services.audit import record
from app.services.jobs import create_job

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# ----- replay dedup ----------------------------------------------------
#
# Paperless workflows re-fire on edits, and a re-posted id is
# re-analyzed as soon as the previous run finishes (skip_active in
# services/jobs.py only brakes CONCURRENT runs) — a stuck retry loop
# would re-analyze forever. In-process map (single-process design, like
# the login throttle): document id -> monotonic deadline until which
# repeats are acknowledged but not re-enqueued.

_monotonic = time.monotonic  # test seam
_recent: dict[int, float] = {}
# Hard bound on tracked ids; oldest deadlines are evicted first (they
# were about to expire anyway).
_RECENT_MAX = 4096


def _dedup_partition(ids: list[int], window: int) -> tuple[list[int], list[int]]:
    """Split ids into (fresh, duplicates) against the recently-seen map
    and mark the fresh ones as seen. ``window <= 0`` disables dedup."""
    now = _monotonic()
    for k in [k for k, deadline in _recent.items() if deadline <= now]:
        _recent.pop(k, None)
    if window <= 0:
        return list(ids), []
    fresh = [i for i in ids if _recent.get(i, 0.0) <= now]
    dups = [i for i in ids if i not in fresh]
    for i in fresh:
        _recent[i] = now + window
    if len(_recent) > _RECENT_MAX:
        for k, _ in sorted(_recent.items(), key=lambda kv: kv[1])[
            : len(_recent) - _RECENT_MAX
        ]:
            _recent.pop(k, None)
    return fresh, dups


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


async def _read_body_capped(request: Request, limit: int) -> bytes:
    """Body with a hard size cap: the declared Content-Length is
    checked first (cheap refusal), then the cap is enforced WHILE
    streaming — a lying or absent header must not buy a free upload."""
    declared = request.headers.get("Content-Length", "")
    if declared.isascii() and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "webhook body too large")
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > limit:
            raise HTTPException(413, "webhook body too large")
    return raw


@router.post("/paperless", status_code=202)
async def paperless_webhook(
    request: Request,
    response: Response,
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
    actor_var.set("system")

    raw = await _read_body_capped(request, cfg.max_body_bytes)
    try:
        body = json.loads(raw)
    except ValueError:  # tolerate non-JSON bodies
        body = {}
    ids = _extract_document_ids(body)
    if not ids:
        raise HTTPException(422, "no document id found in webhook body")
    if len(ids) > cfg.max_documents:
        # Paperless posts ONE document per workflow event — a flood of
        # ids is misuse, not a big batch. Audited: this is the kind of
        # thing an operator wants to see.
        await record(
            db, "webhook", "rejected",
            reason="too_many_documents", count=len(ids), limit=cfg.max_documents,
        )
        await db.commit()
        raise HTTPException(
            422,
            f"too many document ids in webhook body "
            f"({len(ids)} > {cfg.max_documents})",
        )

    ids, duplicates = _dedup_partition(ids, cfg.dedup_window_seconds)
    if duplicates:
        await record(db, "webhook", "deduplicated", documents=duplicates)
    if not ids:
        # Everything was recently accepted — acknowledge honestly (the
        # sender did nothing wrong) without re-enqueuing.
        await db.commit()
        response.status_code = 200
        return {
            "status": "duplicate",
            "queued_documents": [],
            "duplicate_documents": duplicates,
            "session_ids": [],
            "job_id": None,
        }

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
    return {
        "status": "queued",
        "queued_documents": ids,
        "duplicate_documents": duplicates,
        "session_ids": session_ids,
        "job_id": job.id,
    }
