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

import asyncio
import logging
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.db.models import (
    AgentKind,
    EntityType,
    Job,
    JobStatus,
    QueueLane,
    Session,
    SessionPhase,
    SessionStatus,
    Step,
    StepKind,
    StepState,
)
from app.paperless import PaperlessClient
from app.paperless.taxonomy import TAXONOMY
from app.services.audit import record
from app.services.steps import create_step, notify_steps

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
) -> tuple[list[int], dict[int, str]]:
    """Deliberately deterministic scopes only — explicit ids, a tag, the
    inbox, or untagged. Jobs are never defined by a full-text search.
    Returns (ids, titles) — titles feed human session names; ids never
    surface in the UI."""
    if document_ids:
        return list(dict.fromkeys(document_ids)), {}
    if inbox:
        inbox_tags = [t.id for t in await paperless.list_tags() if t.is_inbox_tag]
        if not inbox_tags:
            return [], {}
        page = await paperless.search_documents(tag_ids=inbox_tags, page_size=100)
    elif tag_id:
        page = await paperless.search_documents(tag_ids=[tag_id], page_size=100)
    else:
        page = await paperless.search_documents(
            tags_none=True if untagged_only else None,
            page_size=100,
        )
    ids = list(page.all) if page.all else [d.id for d in page.results]
    titles = {d.id: d.title for d in page.results if d.title}
    return ids, titles


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
    ids, titles = await resolve_documents(
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
    # Users see names, not numbers — resolve missing titles (explicit-id
    # scopes) and derive a human job label.
    for doc_id in ids:
        if doc_id not in titles:
            try:
                titles[doc_id] = (await paperless.get_document(doc_id)).title
            except Exception:  # noqa: BLE001 — label is cosmetic, never fatal
                titles[doc_id] = ""
    if inbox:
        label = "Inbox"
    elif untagged_only:
        label = "Untagged documents"
    elif tag_id:
        try:
            label = f"Tag: {(await paperless.get_tag(tag_id)).name}"
        except Exception:  # noqa: BLE001
            label = "Tag"
    elif len(ids) == 1:
        label = titles.get(ids[0]) or "1 document"
    else:
        label = f"{len(ids)} selected documents"
    params["label"] = label

    if instructions:
        params["instructions"] = instructions
    if trigger:
        params["trigger"] = trigger
    job = Job(kind=kind, params=params, total=len(ids))
    db.add(job)
    await db.flush()

    # ONE transaction for the whole job: the job row, every session and
    # every step commit together (workers wake only after the commit).
    steps = []
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
            title=titles.get(doc_id) or f"Document {doc_id}",
        )
        db.add(session)
        await db.flush()
        steps.append(
            await create_step(
                db, session, StepKind.ocr if redo_ocr else StepKind.analysis,
                lane=lane, commit=False,
            )
        )
    await record(
        db, "job", "created",
        job_id=job.id, job_kind=kind, documents=ids, skipped_active=skipped,
        apply_policy=apply_policy, redo_ocr=redo_ocr,
        scope={"inbox": inbox, "tag_id": tag_id, "untagged_only": untagged_only,
               "document_ids": document_ids},
    )
    await db.commit()
    notify_steps(steps)
    return job, ids


async def create_entities_job(
    db: AsyncSession,
    paperless: PaperlessClient,
    *,
    entity_type: EntityType,
    entity_ids: list[int],
    instructions: str | None = None,
) -> tuple[Job, list[int]]:
    """Bulk taxonomy review: ONE job, one session per entity — same
    machinery as document jobs (progress, cancellation, retry), so the
    UI never loops POSTs client-side."""
    spec = TAXONOMY.get(entity_type.value)
    names: dict[int, str] = {}
    if spec is not None:
        for eid in dict.fromkeys(entity_ids):
            try:
                names[eid] = (await spec.get(paperless, eid)).name
            except Exception:  # noqa: BLE001 — names are cosmetic
                names[eid] = ""
    ids = list(dict.fromkeys(entity_ids))
    type_label = entity_type.value.replace("_", " ")
    params: dict[str, Any] = {
        "entity_type": str(entity_type.value),
        "entity_ids": ids,
        "label": (
            f"{type_label.capitalize()}: {names.get(ids[0]) or ids[0]}"
            if len(ids) == 1
            else f"{len(ids)} {type_label}s"
        ),
    }
    if instructions:
        params["instructions"] = instructions
    job = Job(kind="analyze_entities", params=params, total=len(ids))
    db.add(job)
    await db.flush()

    steps = []
    for eid in ids:
        session = Session(
            agent_kind=AgentKind(entity_type.value),
            entity_type=entity_type,
            entity_id=eid,
            job_id=job.id,
            params={**({"instructions": instructions} if instructions else {})},
            title=names.get(eid) or f"{type_label} {eid}",
        )
        db.add(session)
        await db.flush()
        steps.append(
            await create_step(
                db, session, StepKind.analysis, lane=QueueLane.batch, commit=False
            )
        )
    await record(
        db, "job", "created",
        job_id=job.id, job_kind="analyze_entities",
        entity_type=str(entity_type.value), entity_ids=ids,
    )
    await db.commit()
    notify_steps(steps)
    return job, ids


async def create_entity_job(
    db: AsyncSession,
    paperless: PaperlessClient,
    *,
    agent_kind: AgentKind,
    entity_type: EntityType,
    entity_id: int,
    instructions: str | None = None,
) -> tuple[Job, Session]:
    """A single taxonomy review is a tracked job too (total=1) — it
    runs on the interactive lane."""
    spec = TAXONOMY.get(entity_type.value)
    try:
        entity_name = (await spec.get(paperless, entity_id)).name if spec else ""
    except Exception:  # noqa: BLE001 — names are cosmetic, never fatal
        entity_name = ""
    type_label = entity_type.value.replace("_", " ")
    params: dict[str, Any] = {
        "entity_type": str(entity_type.value),
        "entity_id": entity_id,
        "label": f"{type_label.capitalize()}: {entity_name}" if entity_name
                 else type_label.capitalize(),
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
        title=entity_name or f"{type_label} {entity_id}",
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


# Serializes job-counter updates (lost-update race between workers).
_job_update_lock = asyncio.Lock()


async def update_job(db: AsyncSession, job_id: int) -> None:
    """Job counters: a session counts as done when it reached a
    terminal, non-blocked position (done/failed)."""
    async with _job_update_lock:
        job = await db.get(Job, job_id)
        if job is None:
            return
        sessions = (
            await db.scalars(
                select(Session)
                .where(Session.job_id == job_id)
                .options(defer(Session.message_history))
            )
        ).all()
        done = failed = unfinished = 0
        for s in sessions:
            if s.status == SessionStatus.failed:
                # Failed only counts as final when no retry is pending.
                has_pending = await db.scalar(
                    select(func.count())
                    .select_from(Step)
                    .where(
                        Step.session_id == s.id,
                        Step.state.in_([StepState.pending, StepState.running]),
                    )
                )
                if has_pending:
                    unfinished += 1
                else:
                    failed += 1
            elif s.phase == SessionPhase.done:
                done += 1
            else:
                unfinished += 1
        job.done, job.failed = done, failed
        if job.status != JobStatus.cancelled:
            job.status = (
                JobStatus.running
                if unfinished
                else (JobStatus.completed if done else JobStatus.failed)
            )
        await db.flush()


