"""Jobs: EVERY analysis run is tracked as a job — a single manual
analysis, a bulk run, or a webhook ingest. The job is the execution
record (progress, failures); sessions link to it.

Lanes: single manual analyses run on the interactive lane, bulk and
webhook work on the batch lane — tracking does not change scheduling.

Per-job ``apply_policy``:
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
    QueueLane,
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
    tag_id: int | None = None,
    inbox: bool = False,
    untagged_only: bool = False,
) -> list[int]:
    """Deliberately deterministic scopes only — explicit ids, a tag, the
    inbox, or untagged. Jobs are never defined by a full-text search."""
    if document_ids:
        return list(dict.fromkeys(document_ids))
    if inbox:
        inbox_tags = [t.id for t in await paperless.list_tags() if t.is_inbox_tag]
        if not inbox_tags:
            return []
        page = await paperless.search_documents(tag_ids=inbox_tags, page_size=100)
    elif tag_id:
        page = await paperless.search_documents(tag_ids=[tag_id], page_size=100)
    else:
        page = await paperless.search_documents(
            tags_none=True if untagged_only else None,
            page_size=100,
        )
    ids = list(page.all) if page.all else [d.id for d in page.results]
    return ids


async def create_job(
    db: AsyncSession,
    paperless: PaperlessClient,
    *,
    document_ids: list[int] | None = None,
    tag_id: int | None = None,
    inbox: bool = False,
    untagged_only: bool = False,
    redo_ocr: bool = False,
    apply_policy: Literal["review", "auto"] = "review",
    instructions: str | None = None,
    skip_active: bool = True,
    kind: str = "bulk_analyze",
    lane: QueueLane = QueueLane.batch,
    trigger: str | None = None,
) -> tuple[Job, list[int]]:
    """Create the job + sessions + queue items. Returns (job, doc_ids)."""
    ids = await resolve_documents(
        paperless,
        document_ids=document_ids,
        tag_id=tag_id,
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
        "tag_id": tag_id,
        "inbox": inbox,
        "untagged_only": untagged_only,
        "redo_ocr": redo_ocr,
        "apply_policy": apply_policy,
        "skipped_active": skipped,
    }
    if instructions:
        params["instructions"] = instructions
    if trigger:
        params["trigger"] = trigger
    job = Job(kind=kind, params=params, total=len(ids))
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
                **({"trigger": trigger} if trigger else {}),
            },
            title=f"Document #{doc_id} analysis",
        )
        db.add(session)
        await db.flush()
        await create_step(
            db, session, StepKind.ocr if redo_ocr else StepKind.analysis,
            lane=lane,
        )
    await record(
        db, "job", "created",
        job_id=job.id, job_kind=kind, documents=ids, skipped_active=skipped,
        apply_policy=apply_policy, redo_ocr=redo_ocr,
        scope={"inbox": inbox, "tag_id": tag_id, "untagged_only": untagged_only,
               "document_ids": document_ids},
    )
    await db.commit()
    return job, ids


async def create_entity_job(
    db: AsyncSession,
    *,
    agent_kind: AgentKind,
    entity_type: EntityType,
    entity_id: int,
    instructions: str | None = None,
) -> tuple[Job, Session]:
    """A single taxonomy review is a tracked job too (total=1) — it
    runs on the interactive lane."""
    params: dict[str, Any] = {
        "entity_type": str(entity_type.value),
        "entity_id": entity_id,
    }
    if instructions:
        params["instructions"] = instructions
    job = Job(kind="analyze_entity", params=params, total=1)
    db.add(job)
    await db.flush()

    session = Session(
        agent_kind=agent_kind,
        entity_type=entity_type,
        entity_id=entity_id,
        job_id=job.id,
        params={**({"instructions": instructions} if instructions else {})},
        title=f"{entity_type.value.replace('_', ' ')} #{entity_id} review",
    )
    db.add(session)
    await db.flush()
    await create_step(
        db, session, StepKind.analysis, lane=QueueLane.interactive
    )
    await record(
        db, "job", "created",
        job_id=job.id, job_kind="analyze_entity",
        entity_type=str(entity_type.value), entity_id=entity_id,
    )
    await db.commit()
    return job, session
