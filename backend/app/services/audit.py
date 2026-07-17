"""Audit trail helper — one call site per notable event."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog

log = logging.getLogger(__name__)


async def record(
    db: AsyncSession,
    kind: str,
    action: str,
    commit: bool = False,
    actor: str | None = None,
    **detail: Any,
) -> None:
    """Append an audit entry (actor defaults to the ambient context —
    "user" inside API requests, "system" in background work). Never
    raises — the audit trail must not break the operation it
    describes."""
    from app.services.actor import current_actor

    try:
        db.add(AuditLog(kind=kind, action=action, actor=actor or current_actor(), detail=detail))
        if commit:
            await db.commit()
        else:
            await db.flush()
    except Exception:  # noqa: BLE001
        log.exception("audit record failed (%s/%s)", kind, action)
