"""Paperless traffic -> audit log, decoupled.

The paperless client has no DB session, so it enqueues call records
into an in-memory buffer; a background writer (started in the app
lifespan) drains the buffer into audit_log rows. Bounded buffer — if
the writer can't keep up, oldest records drop rather than blocking
paperless calls.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog

log = logging.getLogger(__name__)

_buffer: deque[dict[str, Any]] = deque(maxlen=2000)


def enqueue(record: dict[str, Any]) -> None:
    _buffer.append(record)


async def drain(db: AsyncSession) -> int:
    """Flush buffered records into audit rows (caller commits)."""
    n = 0
    while _buffer:
        r = _buffer.popleft()
        db.add(
            AuditLog(
                kind="paperless",
                action=r.pop("action"),
                actor=r.pop("actor"),
                ts=r.pop("ts"),
                detail=r,
            )
        )
        n += 1
    return n


async def writer_loop(interval: float = 2.0) -> None:
    from app.db.session import session_scope

    while True:
        await asyncio.sleep(interval)
        if not _buffer:
            continue
        try:
            async with session_scope() as db:
                await drain(db)
                await db.commit()
        except Exception:  # noqa: BLE001 — logging must never crash the app
            log.exception("paperless traffic log flush failed")
