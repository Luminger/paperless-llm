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
    all_documents: bool = False,
) -> tuple[list[int], dict[int, str]]:
    """Deliberately deterministic scopes only — explicit ids, a tag, the
    inbox, untagged, or the whole archive. Jobs are never defined by a
    full-text search. Returns (ids, titles) — titles feed human session
    names; ids never surface in the UI."""
    if document_ids:
        return list(dict.fromkeys(document_ids)), {}
    if inbox:
        inbox_tags = [t.id for t in await paperless.list_tags() if t.is_inbox_tag]
        if not inbox_tags:
            return [], {}
        return await _all_matching(paperless, tag_ids=inbox_tags)
    if tag_id:
        return await _all_matching(paperless, tag_ids=[tag_id])
    if all_documents:
        return await _all_matching(paperless)
    return await _all_matching(
        paperless, tags_none=True if untagged_only else None
    )


async def _all_matching(
    paperless: PaperlessClient, **kwargs: Any
) -> tuple[list[int], dict[int, str]]:
    """EVERY matching document id (+titles), paginated. AUDIT API-F3:
    `Page.all` is only guaranteed on full-text queries — relying on it
    for plain filtered listings silently capped bulk jobs at 100
    documents on paperless versions that omit it."""
    first = await paperless.search_documents(page_size=100, **kwargs)
    ids = [d.id for d in first.results]
    titles = {d.id: d.title for d in first.results if d.title}
    # Bounded by count, not open-ended: documents deleted mid-iteration
    # must not turn this into a 404 loop (same rule as apply.py's
    # _docs_referencing).
    total_pages = -(-first.count // 100)
    for page_no in range(2, total_pages + 1):
        more = await paperless.search_documents(
            page=page_no, page_size=100, **kwargs
        )
        if not more.results:
            break
        ids += [d.id for d in more.results]
        titles.update({d.id: d.title for d in more.results if d.title})
    if first.all and len(first.all) > len(ids):
        ids = list(first.all)  # server view is authoritative when larger
    return list(dict.fromkeys(ids)), titles


async def processed_document_ids(db: AsyncSession) -> set[int]:
    """Documents that went through a COMPLETED metadata analysis — the
    corpus-curation notion of "done". OCR-only sessions don't count
    (they fix text, not metadata)."""
    rows = (
        await db.execute(
            select(Session.entity_id, Session.params).where(
                Session.entity_type == EntityType.document,
                Session.phase == SessionPhase.done,
            )
        )
    ).all()
    return {
        eid for eid, params in rows
        if eid is not None and not (params or {}).get("ocr_only")
    }


async def resolve_next_batch(
    db: AsyncSession,
    paperless: PaperlessClient,
    size: int,
) -> list[int]:
    """The next ``size`` documents that never had a completed analysis,
    oldest first — deterministic, so "work the corpus in batches" is
    just pressing the same button until it's done."""
    done = await processed_document_ids(db)
    picked: list[int] = []
    page_no = 1
    while len(picked) < size:
        page = await paperless.search_documents(
            ordering="created", page=page_no, page_size=100
        )
        if not page.results:
            break
        for d in page.results:
            if d.id not in done and len(picked) < size:
                picked.append(d.id)
        if len(page.results) < 100:
            break
        page_no += 1
    return picked


async def create_job(
    db: AsyncSession,
    paperless: PaperlessClient,
    *,
    document_ids: list[int] | None = None,
    tag_id: int | None = None,
    inbox: bool = False,
    untagged_only: bool = False,
    all_documents: bool = False,
    redo_ocr: bool = False,
    ocr_only: bool = False,
    apply_policy: Literal["review", "auto"] = "review",
    instructions: str | None = None,
    skip_active: bool = True,
    kind: str = "bulk_analyze",
    lane: QueueLane = QueueLane.batch,
    trigger: str | None = None,
    label: str | None = None,
) -> tuple[Job, list[int]]:
    """Create the job + sessions + queue items. Returns (job, doc_ids).

    ``ocr_only``: the corpus-rehab job — each document is re-OCRed and
    the pipeline STOPS there (gate in review mode, direct journaled
    write in auto mode). No analysis follows. Born-digital documents
    whose text layer matches the stored content resolve their gate
    automatically in either mode (see _exec_ocr's auto_native path)."""
    if ocr_only:
        kind = "bulk_ocr"
        redo_ocr = True
    ids, titles = await resolve_documents(
        paperless,
        document_ids=document_ids,
        tag_id=tag_id,
        inbox=inbox,
        untagged_only=untagged_only,
        all_documents=all_documents,
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
        "all_documents": all_documents,
        "redo_ocr": redo_ocr,
        "ocr_only": ocr_only,
        "apply_policy": apply_policy,
        "skipped_active": skipped,
    }
    # Users see names, not numbers — resolve missing titles (explicit-id
    # scopes) in BATCHES of 100 via id__in (AUDIT SV-M3: one sequential
    # GET per document made a 10k-doc job time out its own POST).
    missing = [d for d in ids if d not in titles]
    for i in range(0, len(missing), 100):
        chunk = missing[i : i + 100]
        try:
            pg = await paperless.search_documents(document_ids=chunk, page_size=100)
            titles.update({d.id: d.title for d in pg.results if d.title})
        except Exception:  # noqa: BLE001 — labels are cosmetic, never fatal
            pass
    for doc_id in ids:
        titles.setdefault(doc_id, "")
    if label is None:
        if inbox:
            label = "Inbox"
        elif all_documents:
            label = "All documents"
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
                **({"ocr_only": True} if ocr_only else {}),
                "apply_policy": apply_policy,
                **({"instructions": instructions} if instructions else {}),
                **({"trigger": trigger} if trigger else {}),
            },
            # Sessions are named for the RUN, not the entity — entity
            # names go stale (the analysis itself renames documents);
            # lists resolve them live instead.
            title="OCR pass" if ocr_only else "Analysis",
        )
        db.add(session)
        await db.flush()
        # Analysis steps carry the user's instructions in their INPUT —
        # the UI renders them as the user's own box on the turn. (An
        # OCR-first pipeline stamps them on the analysis step at gate
        # resolution instead.) OCR-only steps take them as OCR guidance
        # directly, and are marked so phase derivation knows the
        # pipeline ENDS at the gate.
        step_input: dict[str, Any] | None
        if ocr_only:
            step_input = {
                "ocr_only": True,
                **({"instructions": instructions} if instructions else {}),
            }
        elif instructions and not redo_ocr:
            step_input = {"instructions": instructions}
        else:
            step_input = None
        steps.append(
            await create_step(
                db, session, StepKind.ocr if redo_ocr else StepKind.analysis,
                step_input, lane=lane, commit=False,
            )
        )
    await record(
        db, "job", "created",
        job_id=job.id, job_kind=kind, documents=ids, skipped_active=skipped,
        apply_policy=apply_policy, redo_ocr=redo_ocr,
        scope={"inbox": inbox, "tag_id": tag_id, "untagged_only": untagged_only,
               "all_documents": all_documents, "document_ids": document_ids},
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
            title="Review",
        )
        db.add(session)
        await db.flush()
        steps.append(
            await create_step(
                db, session, StepKind.analysis,
                {"instructions": instructions} if instructions else None,
                lane=QueueLane.batch, commit=False,
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
        title="Review",
    )
    db.add(session)
    await db.flush()
    await create_step(
        db, session, StepKind.analysis,
        {"instructions": instructions} if instructions else None,
        lane=QueueLane.interactive,
    )
    await record(
        db, "job", "created",
        job_id=job.id, job_kind="analyze_entity",
        entity_type=str(entity_type.value), entity_id=entity_id,
    )
    await db.commit()
    return job, session


# AUDIT SV-M1: job state is COMPUTED from the sessions at read time —
# there is no stored-counter maintenance anymore (the old update_job
# scanned every session of the job under a global lock on EVERY step
# finalize, and its counters still went stale). These two functions are
# THE derivation; every reader goes through them.


async def live_job_counts(
    db: AsyncSession, job_ids: list[int]
) -> dict[int, tuple[int, int, int, int]]:
    """(done, failed, stopped, unfinished) per job, computed FROM THE
    SESSIONS at read time — stored counters can go stale (e.g. a gate
    resolved without a worker touching the job afterwards), the
    sessions can't. ``stopped`` = user-cancelled runs: neither done nor
    failed, and NOT unfinished (they won't run again on their own)."""
    if not job_ids:
        return {}
    rows = (
        await db.execute(
            select(Session.id, Session.job_id, Session.status, Session.phase).where(
                Session.job_id.in_(job_ids)
            )
        )
    ).all()
    failed_ids = [sid for sid, _j, status, _p in rows if status == SessionStatus.failed]
    retrying = set(
        (
            await db.scalars(
                select(Step.session_id).where(
                    Step.session_id.in_(failed_ids or [0]),
                    Step.state.in_((StepState.pending, StepState.running)),
                )
            )
        ).all()
    )
    out: dict[int, tuple[int, int, int, int]] = dict.fromkeys(job_ids, (0, 0, 0, 0))
    for sid, job_id, status, phase in rows:
        done, failed, stopped, unfinished = out[job_id]
        if status == SessionStatus.failed and sid not in retrying:
            failed += 1
        elif phase == SessionPhase.done:
            done += 1
        elif phase == SessionPhase.stopped and sid not in retrying:
            stopped += 1
        else:
            unfinished += 1
        out[job_id] = (done, failed, stopped, unfinished)
    return out


def apply_live(job_out, counts: tuple[int, int, int, int]):
    """Stamp derived counts/status onto a JobOut-shaped object.
    ``cancelled`` and ``paused`` are sticky (the stored statuses that
    matter); everything else derives from the sessions. A job whose
    remaining sessions were all STOPPED is not "running" — it settles
    as completed/failed by what actually finished."""
    done, failed, stopped, unfinished = counts
    job_out.done, job_out.failed, job_out.stopped = done, failed, stopped
    if job_out.status not in (JobStatus.cancelled, JobStatus.paused):
        job_out.status = (
            JobStatus.running
            if unfinished
            else (JobStatus.completed if done else JobStatus.failed)
        )
    return job_out


