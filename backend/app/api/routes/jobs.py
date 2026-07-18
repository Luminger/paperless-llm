"""Bulk jobs and queue/dashboard stats."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.api.deps import get_paperless
from app.api.pagination import count_of, paginate
from app.api.schemas import (
    CorpusOut,
    JobAttentionOut,
    JobCreate,
    JobDetailOut,
    JobOut,
    JobPage,
    SessionOut,
    StatsOut,
)
from app.db.models import (
    Job,
    JobStatus,
    Proposal,
    ProposalStatus,
    Session,
    SessionPhase,
    SessionStatus,
    Step,
    StepState,
)
from app.db.session import get_session
from app.paperless import PaperlessClient
from app.services.jobs import create_job as create_job_service

router = APIRouter(prefix="/api", tags=["jobs"])


@router.post("/jobs")
async def create_job(
    body: JobCreate,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> JobOut:
    if body.entity_type and body.entity_ids:
        from app.db.models import EntityType
        from app.services.jobs import create_entities_job

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
        from app.services.jobs import resolve_next_batch

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
                    Proposal.kind != "replace_content",
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
    from app.services.jobs import processed_document_ids

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
    return JobPage(
        count=win.count,
        page=win.page,
        page_size=win.page_size,
        results=[JobOut.model_validate(j) for j in (await db.scalars(q)).all()],
    )


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, db: AsyncSession = Depends(get_session)) -> JobDetailOut:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    out = JobDetailOut.model_validate(job)
    sessions = (
        await db.scalars(
            select(Session)
            .options(defer(Session.message_history))
            .where(Session.job_id == job_id)
            .order_by(Session.id)
        )
    ).all()
    pending_case = case(
        (
            (Proposal.status == ProposalStatus.pending)
            & (Proposal.kind != "replace_content"),
            1,
        ),
        else_=0,
    )
    counts = {
        sid: (n, int(pending or 0))
        for sid, n, pending in (
            await db.execute(
                select(Proposal.session_id, func.count(), func.sum(pending_case))
                .where(Proposal.session_id.in_([s.id for s in sessions] or [0]))
                .group_by(Proposal.session_id)
            )
        ).all()
    }
    out.sessions = []
    for s in sessions:
        item = SessionOut.model_validate(s)
        item.proposal_count, item.pending_proposal_count = counts.get(s.id, (0, 0))
        out.sessions.append(item)
    return out


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, db: AsyncSession = Depends(get_session)) -> JobOut:
    """Cancel all still-pending work of a job. Running steps finish."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status in (JobStatus.completed, JobStatus.cancelled):
        raise HTTPException(409, f"job is already {job.status}")
    from app.services.steps import cancel_job_steps

    await cancel_job_steps(db, job_id)
    job.status = JobStatus.cancelled
    await db.commit()
    return JobOut.model_validate(job)


@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_session)) -> StatsOut:
    pending_proposals = await db.scalar(
        select(func.count())
        .select_from(Proposal)
        .where(Proposal.status == ProposalStatus.pending, Proposal.kind != "replace_content")
    )
    active_sessions = await db.scalar(
        select(func.count())
        .select_from(Session)
        .where(
            Session.phase.in_(
                [SessionPhase.queued, SessionPhase.ocr_running,
                 SessionPhase.ocr_review, SessionPhase.analyzing]
            ),
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
    active_jobs = await db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.status.in_([JobStatus.queued, JobStatus.running]))
    )
    from app.services.counters import get_all

    return StatsOut(
        pending_proposals=pending_proposals or 0,
        active_sessions=active_sessions or 0,
        queue_pending={str(k.value): v for k, v in lanes.items()},
        active_jobs=active_jobs or 0,
        lifetime=await get_all(db),
    )
