"""Bulk jobs and queue/dashboard stats."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_paperless
from app.api.pagination import count_of, paginate
from app.api.schemas import (
    CorpusOut,
    JobAttentionOut,
    JobCreate,
    JobOut,
    JobPage,
    StatsOut,
)
from app.db.models import (
    EntityType,
    Job,
    JobStatus,
    Proposal,
    ProposalStatus,
    QueueLane,
    Session,
    SessionPhase,
    SessionStatus,
    Step,
    StepState,
)
from app.db.session import get_session
from app.paperless import PaperlessClient
from app.proposals.kinds import visible
from app.services.audit import record
from app.services.counters import get_all
from app.services.jobs import (
    ACTIVE_PHASES,
    apply_live,
    create_entities_job,
    live_job_counts,
    processed_document_ids,
    resolve_next_batch,
)
from app.services.jobs import create_job as create_job_service
from app.services.steps import (
    StepActionError,
    cancel_job_steps,
    publish_step_changed,
    workers,
)
from app.services.steps import retry_step as engine_retry

router = APIRouter(prefix="/api", tags=["jobs"])


@router.post("/jobs")
async def create_job(
    body: JobCreate,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> JobOut:
    if body.entity_type and body.entity_ids:
        job, _ = await create_entities_job(
            db,
            paperless,
            entity_type=EntityType(body.entity_type),
            entity_ids=body.entity_ids,
            instructions=body.instructions,
        )
        return JobOut.model_validate(job)
    document_ids = body.document_ids
    label: str | None = None
    if body.next_batch:
        document_ids = await resolve_next_batch(db, paperless, body.next_batch)
        if not document_ids:
            raise HTTPException(
                422, "every document has been analyzed — the corpus is done"
            )
        label = f"Corpus batch ({len(document_ids)} documents)"
    if not (
        document_ids
        or body.tag_id
        or body.inbox
        or body.untagged_only
        or body.all_documents
    ):
        raise HTTPException(422, "empty selection")
    job, ids = await create_job_service(
        db,
        paperless,
        document_ids=document_ids,
        tag_id=body.tag_id,
        inbox=body.inbox,
        untagged_only=body.untagged_only,
        all_documents=body.all_documents,
        redo_ocr=body.redo_ocr,
        ocr_only=body.ocr_only,
        apply_policy=body.apply_policy,
        instructions=body.instructions,
        label=label,
    )
    return JobOut.model_validate(job)


@router.get("/jobs/{job_id}/attention")
async def job_attention(
    job_id: int,
    after: int | None = None,
    db: AsyncSession = Depends(get_session),
) -> JobAttentionOut:
    """Which session in this job needs the user next? "Needs" means an
    open gate (awaiting_user step) or a pending proposal. ``after``
    excludes the session being viewed and continues past it, wrapping
    to the start — so "Next" walks the whole job."""
    if await db.get(Job, job_id) is None:
        raise HTTPException(404, "job not found")
    session_ids = (
        await db.scalars(select(Session.id).where(Session.job_id == job_id))
    ).all()
    if not session_ids:
        return JobAttentionOut()
    gated = set(
        (
            await db.scalars(
                select(Step.session_id).where(
                    Step.session_id.in_(session_ids),
                    Step.state == StepState.awaiting_user,
                )
            )
        ).all()
    )
    pending = set(
        (
            await db.scalars(
                select(Proposal.session_id).where(
                    Proposal.session_id.in_(session_ids),
                    Proposal.status == ProposalStatus.pending,
                    visible(),
                )
            )
        ).all()
    )
    waiting = sorted(gated | pending)
    others = [i for i in waiting if i != after]
    next_id = next((i for i in others if after is None or i > after), None)
    if next_id is None and others:
        next_id = others[0]  # wrap around
    return JobAttentionOut(next_session_id=next_id, remaining=len(waiting))


@router.get("/corpus")
async def corpus_status(
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> CorpusOut:
    """How much of the archive ever went through a completed analysis —
    feeds the dashboard's batch-by-batch curation block."""
    page = await paperless.search_documents(page_size=1)
    return CorpusOut(total=page.count, processed=len(await processed_document_ids(db)))


@router.get("/jobs")
async def list_jobs(
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_session),
) -> JobPage:
    win, q = await paginate(
        db, select(Job).order_by(Job.id.desc()), count_of(Job),
        page=page, page_size=page_size,
    )
    jobs = [JobOut.model_validate(j) for j in (await db.scalars(q)).all()]
    live = await live_job_counts(db, [j.id for j in jobs])
    return JobPage(
        count=win.count,
        page=win.page,
        page_size=win.page_size,
        results=[apply_live(j, live.get(j.id, (0, 0, 0, 0))) for j in jobs],
    )


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: int,
    db: AsyncSession = Depends(get_session),
) -> JobOut:
    """The job itself. Its sessions come from GET /api/sessions?job_id=
    — the ONE paginated, filterable session list."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    out = JobOut.model_validate(job)
    return apply_live(out, (await live_job_counts(db, [job.id])).get(job.id, (0, 0, 0, 0)))


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: int, db: AsyncSession = Depends(get_session)) -> JobOut:
    """Pause: workers stop claiming this job's steps. Running steps
    finish and keep their results; nothing new starts until resume.
    A single job-row flip — no step state is rewritten."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    live_out = apply_live(
        JobOut.model_validate(job),
        (await live_job_counts(db, [job.id])).get(job.id, (0, 0, 0, 0)),
    )
    if job.status == JobStatus.paused:
        return live_out
    if live_out.status in (JobStatus.completed, JobStatus.cancelled):
        raise HTTPException(409, f"job is already {live_out.status}")
    job.status = JobStatus.paused
    await record(db, "job", "paused", job_id=job.id)
    await db.commit()
    return apply_live(
        JobOut.model_validate(job),
        (await live_job_counts(db, [job.id])).get(job.id, (0, 0, 0, 0)),
    )


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: int, db: AsyncSession = Depends(get_session)) -> JobOut:
    """Resume a paused job: its pending steps become claimable again."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != JobStatus.paused:
        raise HTTPException(409, "job is not paused")
    job.status = JobStatus.queued  # derived status recomputes at read
    await record(db, "job", "resumed", job_id=job.id)
    await db.commit()
    for lane in QueueLane:
        workers.wake(lane)
    return apply_live(
        JobOut.model_validate(job),
        (await live_job_counts(db, [job.id])).get(job.id, (0, 0, 0, 0)),
    )


class JobRetryRequest(BaseModel):
    # None = every failed/cancelled/backoff session of the job.
    session_ids: list[int] | None = None


class JobRetryOut(BaseModel):
    retried: int


@router.post("/jobs/{job_id}/retry")
async def retry_job_sessions(
    job_id: int,
    body: JobRetryRequest | None = None,
    db: AsyncSession = Depends(get_session),
) -> JobRetryOut:
    """Bulk retry: run the latest failed/cancelled (or backoff-pending)
    step of each targeted session again, now. Targets default to every
    session of the job that has something to retry; explicit
    session_ids narrow it (the list multiselect)."""
    if await db.get(Job, job_id) is None:
        raise HTTPException(404, "job not found")
    wanted = (body.session_ids if body else None) or None
    q = (
        select(Step)
        .join(Session, Session.id == Step.session_id)
        .where(Session.job_id == job_id, Step.state != StepState.superseded)
        .order_by(Step.session_id, Step.id)
    )
    if wanted:
        q = q.where(Step.session_id.in_(wanted))
    last_by_session: dict[int, Step] = {}
    for step in (await db.scalars(q)).all():
        last_by_session[step.session_id] = step  # ordered by id: last wins
    retried = 0
    for step in last_by_session.values():
        eligible = step.state in (StepState.failed, StepState.cancelled) or (
            step.state == StepState.pending and step.scheduled_at is not None
        )
        if not eligible:
            continue
        try:
            await engine_retry(db, step)
            retried += 1
        except StepActionError:  # raced into an ineligible state — skip
            continue
    await record(db, "job", "bulk_retry", job_id=job_id, retried=retried,
                 requested=len(wanted) if wanted else None)
    await db.commit()
    return JobRetryOut(retried=retried)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, db: AsyncSession = Depends(get_session)) -> JobOut:
    """Cancel all still-pending work of a job and abort its running
    steps (their in-flight LLM calls are stopped)."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    # The stored status only knows "cancelled"; completion is DERIVED —
    # guard against cancelling an already-finished job with live state.
    live_out = apply_live(
        JobOut.model_validate(job),
        (await live_job_counts(db, [job.id])).get(job.id, (0, 0, 0, 0)),
    )
    if live_out.status in (JobStatus.completed, JobStatus.cancelled):
        raise HTTPException(409, f"job is already {live_out.status}")
    cancelled = await cancel_job_steps(db, job_id)
    job.status = JobStatus.cancelled
    await db.commit()
    # Announce only committed state (AUDIT SV-M2).
    publish_step_changed(cancelled)
    return JobOut.model_validate(job)


@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_session)) -> StatsOut:
    pending_proposals = await db.scalar(
        select(func.count())
        .select_from(Proposal)
        .where(Proposal.status == ProposalStatus.pending, visible())
    )
    active_sessions = await db.scalar(
        select(func.count())
        .select_from(Session)
        .where(
            Session.phase.in_(ACTIVE_PHASES),
            Session.status != SessionStatus.failed,
        )
    )
    lanes = dict(
        (
            await db.execute(
                select(Step.lane, func.count())
                .where(Step.state == StepState.pending)
                .group_by(Step.lane)
            )
        ).all()
    )
    # Computed from the sessions, like everywhere: a job is active while
    # any of its sessions is neither done nor terminally failed.
    active_jobs = await db.scalar(
        select(func.count(func.distinct(Session.job_id))).where(
            Session.job_id.is_not(None),
            Session.phase != SessionPhase.done,
            Session.status != SessionStatus.failed,
            Session.job_id.notin_(
                select(Job.id).where(Job.status == JobStatus.cancelled)
            ),
        )
    )
    return StatsOut(
        pending_proposals=pending_proposals or 0,
        active_sessions=active_sessions or 0,
        queue_pending={str(k.value): v for k, v in lanes.items()},
        active_jobs=active_jobs or 0,
        lifetime=await get_all(db),
    )
