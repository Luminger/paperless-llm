"""Audit log + paperless fetch transparency."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    where = []
    if kind == "changes":
        where.append(AuditLog.kind != "paperless")
    elif kind:
        where.append(AuditLog.kind == kind)
    count = (
        await db.scalar(select(func.count()).select_from(AuditLog).where(*where))
    ) or 0
    rows = (
        await db.scalars(
            select(AuditLog)
            .where(*where)
            .order_by(AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return AuditPage(
        count=count,
        page=page,
        page_size=page_size,
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
