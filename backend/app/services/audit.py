"""Audit trail helper — one call site per notable event."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog

log = logging.getLogger(__name__)


async def record(
    db: AsyncSession, kind: str, action: str, commit: bool = False, **detail: Any
) -> None:
    """Append an audit entry. Never raises — the audit trail must not
    break the operation it describes."""
    try:
        db.add(AuditLog(kind=kind, action=action, detail=detail))
        if commit:
            await db.commit()
        else:
            await db.flush()
    except Exception:  # noqa: BLE001
        log.exception("audit record failed (%s/%s)", kind, action)
