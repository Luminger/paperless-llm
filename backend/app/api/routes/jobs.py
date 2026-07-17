"""Bulk campaigns and queue/dashboard stats."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_paperless
from app.api.schemas import JobCreate, JobDetailOut, JobOut, SessionOut, StatsOut
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
from app.services.campaigns import create_campaign

router = APIRouter(prefix="/api", tags=["jobs"])


@router.post("/jobs")
async def create_job(
    body: JobCreate,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> JobOut:
    if not (body.document_ids or body.query or body.inbox or body.untagged_only):
        raise HTTPException(422, "empty document selection")
    job, ids = await create_campaign(
        db,
        paperless,
        document_ids=body.document_ids,
        query=body.query,
        inbox=body.inbox,
        untagged_only=body.untagged_only,
        redo_ocr=body.redo_ocr,
        apply_policy=body.apply_policy,
        instructions=body.instructions,
    )
    return JobOut.model_validate(job)


@router.get("/jobs")
async def list_jobs(db: AsyncSession = Depends(get_session)) -> list[JobOut]:
    jobs = (
        await db.scalars(select(Job).order_by(Job.id.desc()).limit(100))
    ).all()
    return [JobOut.model_validate(j) for j in jobs]


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, db: AsyncSession = Depends(get_session)) -> JobDetailOut:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    out = JobDetailOut.model_validate(job)
    sessions = (
        await db.scalars(
            select(Session).where(Session.job_id == job_id).order_by(Session.id)
        )
    ).all()
    counts = dict(
        (
            await db.execute(
                select(Proposal.session_id, func.count())
                .where(Proposal.session_id.in_([s.id for s in sessions] or [0]))
                .group_by(Proposal.session_id)
            )
        ).all()
    )
    out.sessions = []
    for s in sessions:
        item = SessionOut.model_validate(s)
        item.proposal_count = counts.get(s.id, 0)
        out.sessions.append(item)
    return out


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, db: AsyncSession = Depends(get_session)) -> JobOut:
    """Cancel all still-pending work of a campaign. Running stages finish."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status in (JobStatus.completed, JobStatus.cancelled):
        raise HTTPException(409, f"job is already {job.status}")
    pending = (
        await db.scalars(
            select(Step)
            .join(Session, Session.id == Step.session_id)
            .where(Session.job_id == job_id, Step.state == StepState.pending)
        )
    ).all()
    from app.services.steps import sync_session

    for step in pending:
        step.state = StepState.cancelled
        step.error = "cancelled with its campaign"
        session = await db.get(Session, step.session_id)
        if session is not None and session.phase != SessionPhase.done:
            await sync_session(db, session)
            if session.status != SessionStatus.failed:
                session.status = SessionStatus.failed
                session.error = "cancelled with its campaign"
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
