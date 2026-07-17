"""Bulk campaigns: resolve a document set, create one session per
document, enqueue their pipelines on the batch lane.

Per-campaign ``apply_policy``:
- ``review`` (default): proposals wait for a human, as always.
- ``auto``: proposals are applied right after the analysis — still
  validated, still journaled, still revertible. The journal is the
  safety net; the policy only skips the waiting.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentKind,
    EntityType,
    Job,
    Session,
    SessionPhase,
    SessionStatus,
    StepKind,
)
from app.paperless import PaperlessClient
from app.services.audit import record
from app.services.steps import create_step

log = logging.getLogger(__name__)

ACTIVE_PHASES = (SessionPhase.queued, SessionPhase.ocr_running,
                 SessionPhase.ocr_review, SessionPhase.analyzing)


async def resolve_documents(
    paperless: PaperlessClient,
    *,
    document_ids: list[int] | None = None,
    query: str | None = None,
    inbox: bool = False,
    untagged_only: bool = False,
) -> list[int]:
    if document_ids:
        return list(dict.fromkeys(document_ids))
    if inbox:
        inbox_tags = [t.id for t in await paperless.list_tags() if t.is_inbox_tag]
        if not inbox_tags:
            return []
        page = await paperless.search_documents(tag_ids=inbox_tags, page_size=100)
    else:
        page = await paperless.search_documents(
            query=query,
            tags_none=True if untagged_only else None,
            page_size=100,
        )
    ids = list(page.all) if page.all else [d.id for d in page.results]
    return ids


async def create_campaign(
    db: AsyncSession,
    paperless: PaperlessClient,
    *,
    document_ids: list[int] | None = None,
    query: str | None = None,
    inbox: bool = False,
    untagged_only: bool = False,
    redo_ocr: bool = False,
    apply_policy: Literal["review", "auto"] = "review",
    instructions: str | None = None,
    skip_active: bool = True,
) -> tuple[Job, list[int]]:
    """Create the job + sessions + queue items. Returns (job, doc_ids)."""
    ids = await resolve_documents(
        paperless,
        document_ids=document_ids,
        query=query,
        inbox=inbox,
        untagged_only=untagged_only,
    )

    skipped: list[int] = []
    if skip_active and ids:
        active = set(
            (
                await db.scalars(
                    select(Session.entity_id).where(
                        Session.entity_type == EntityType.document,
                        Session.entity_id.in_(ids),
                        Session.phase.in_(ACTIVE_PHASES),
                        Session.status != SessionStatus.failed,
                    )
                )
            ).all()
        )
        skipped = [i for i in ids if i in active]
        ids = [i for i in ids if i not in active]

    params: dict[str, Any] = {
        "document_ids": document_ids,
        "query": query,
        "inbox": inbox,
        "untagged_only": untagged_only,
        "redo_ocr": redo_ocr,
        "apply_policy": apply_policy,
        "skipped_active": skipped,
    }
    if instructions:
        params["instructions"] = instructions
    job = Job(kind="bulk_analyze", params=params, total=len(ids))
    db.add(job)
    await db.flush()

    for doc_id in ids:
        session = Session(
            agent_kind=AgentKind.document,
            entity_type=EntityType.document,
            entity_id=doc_id,
            job_id=job.id,
            params={
                "redo_ocr": redo_ocr,
                "apply_policy": apply_policy,
                **({"instructions": instructions} if instructions else {}),
            },
            title=f"Document #{doc_id} analysis",
        )
        db.add(session)
        await db.flush()
        await create_step(
            db, session, StepKind.ocr if redo_ocr else StepKind.analysis
        )
    await record(
        db, "campaign", "created",
        job_id=job.id, documents=ids, skipped_active=skipped,
        apply_policy=apply_policy, redo_ocr=redo_ocr,
        scope={"inbox": inbox, "query": query, "untagged_only": untagged_only,
               "document_ids": document_ids},
    )
    await db.commit()
    return job, ids
