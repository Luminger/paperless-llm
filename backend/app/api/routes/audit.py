"""Audit log + paperless fetch transparency."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.pagination import count_of, paginate
from app.api.schemas import AuditPage, ResourceFetch, SyncStatusOut
from app.db.models import AuditLog
from app.db.session import get_session
from app.paperless.client import fetch_status

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/audit")
async def list_audit(
    page: int = 1,
    page_size: int = 20,
    kind: str | None = None,
    db: AsyncSession = Depends(get_session),
) -> AuditPage:
    where = []
    if kind == "changes":
        # Data changes only — neither raw paperless traffic nor task
        # scheduling noise.
        where.append(AuditLog.kind.notin_(("paperless", "task")))
    elif kind:
        where.append(AuditLog.kind == kind)
    win, q = await paginate(
        db,
        select(AuditLog).where(*where).order_by(AuditLog.id.desc()),
        count_of(AuditLog, *where),
        page=page, page_size=page_size, max_page_size=100,
    )
    rows = (await db.scalars(q)).all()
    return AuditPage(
        count=win.count,
        page=win.page,
        page_size=win.page_size,
        results=[
            {"id": r.id, "ts": r.ts, "kind": r.kind, "action": r.action,
             "actor": r.actor, "detail": r.detail}
            for r in rows
        ],
    )


@router.get("/sync/status")
async def sync_status() -> SyncStatusOut:
    """When the app last fetched each paperless resource and whether a
    fetch is in flight RIGHT NOW — covers every consumer in the process
    (UI proxying, agent tools, pipeline stages)."""
    return SyncStatusOut(
        resources={k: ResourceFetch(**v) for k, v in fetch_status.items()}
    )
