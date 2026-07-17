from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_paperless
from app.api.routes.proposals import _out as proposal_out
from app.api.schemas import (
    AnalyzeEntityRequest,
    AnalyzeRequest,
    MessageRequest,
    OcrGateRequest,
    OcrRerunRequest,
    OcrReviewOut,
    RetryInfo,
    SessionDetailOut,
    SessionOut,
)
from app.db.models import (
    AgentKind,
    EntityType,
    OcrResult,
    Proposal,
    QueueItem,
    QueueLane,
    QueueState,
    Session,
    SessionPhase,
    SessionStatus,
)
from app.db.session import get_session
from app.paperless import PaperlessClient
from app.services import pipeline
from app.services.events import bus
from app.services.queue import enqueue
from app.services.transcript import derive_transcript

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(db: AsyncSession = Depends(get_session)) -> list[SessionOut]:
    q = (
        select(Session, func.count(Proposal.id))
        .outerjoin(Proposal, Proposal.session_id == Session.id)
        .group_by(Session.id)
        .order_by(Session.updated_at.desc())
        .limit(200)
    )
    out: list[SessionOut] = []
    for s, n in (await db.execute(q)).all():
        item = SessionOut.model_validate(s)
        item.proposal_count = n
        out.append(item)
    return out


@router.get("/{session_id}")
async def get_session_detail(
    session_id: int, db: AsyncSession = Depends(get_session)
) -> SessionDetailOut:
    s = await db.get(Session, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    # Build from the base schema: SessionDetailOut.proposals would
    # otherwise collide with the (lazy) ORM relationship of the same name.
    out = SessionDetailOut(
        **SessionOut.model_validate(s).model_dump(),
        transcript=derive_transcript(s.message_history),
    )
    proposals = (
        await db.scalars(
            select(Proposal)
            .where(Proposal.session_id == s.id)
            .options(selectinload(Proposal.applied_change))
            .order_by(Proposal.id)
        )
    ).all()
    out.proposals = [proposal_out(p) for p in proposals]
    item = await db.scalar(
        select(QueueItem)
        .where(QueueItem.session_id == s.id)
        .order_by(QueueItem.id.desc())
        .limit(1)
    )
    if item is not None:
        out.retry = RetryInfo(
            state=item.state.value,
            attempts=item.attempts,
            max_attempts=item.max_attempts,
            next_attempt_at=item.scheduled_at,
        )
    return out


@router.post("/analyze/document/{document_id}")
async def analyze_document(
    document_id: int,
    body: AnalyzeRequest | None = None,
    db: AsyncSession = Depends(get_session),
) -> SessionOut:
    """Start a document analysis pipeline. Returns immediately; the
    session page shows the timeline (OCR gate first when redo_ocr)."""
    body = body or AnalyzeRequest()
    s = Session(
        agent_kind=AgentKind.document,
        entity_type=EntityType.document,
        entity_id=document_id,
        phase=SessionPhase.queued,
        params={
            "redo_ocr": body.redo_ocr,
            **({"instructions": body.instructions} if body.instructions else {}),
        },
        title=f"Document #{document_id} analysis",
    )
    db.add(s)
    await db.flush()
    await enqueue(
        db,
        "start",
        {"session_id": s.id},
        lane=QueueLane.interactive,
        session_id=s.id,
    )
    return SessionOut.model_validate(s)


@router.post("/analyze/{entity_type}/{entity_id}")
async def analyze_entity(
    entity_type: str,
    entity_id: int,
    body: AnalyzeEntityRequest | None = None,
    db: AsyncSession = Depends(get_session),
) -> SessionOut:
    """Start a taxonomy review session (tag/correspondent/document_type).
    No OCR phase — straight to the agent."""
    if entity_type not in ("tag", "correspondent", "document_type"):
        raise HTTPException(422, f"cannot analyze entity type {entity_type!r}")
    body = body or AnalyzeEntityRequest()
    s = Session(
        agent_kind=AgentKind(entity_type),
        entity_type=EntityType(entity_type),
        entity_id=entity_id,
        phase=SessionPhase.queued,
        params=(
            {"instructions": body.instructions} if body.instructions else {}
        ),
        title=f"{entity_type.replace('_', ' ')} #{entity_id} review",
    )
    db.add(s)
    await db.flush()
    await enqueue(
        db, "start", {"session_id": s.id}, lane=QueueLane.interactive, session_id=s.id
    )
    return SessionOut.model_validate(s)


@router.get("/{session_id}/ocr")
async def get_ocr_review(
    session_id: int,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> OcrReviewOut:
    """The OCR gate's diff data: current paperless content vs. new OCR."""
    s = await db.get(Session, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    if s.entity_id is None:
        raise HTTPException(409, "session is not bound to a document")
    latest = await db.scalar(
        select(OcrResult)
        .where(OcrResult.document_id == s.entity_id)
        .order_by(OcrResult.created_at.desc())
        .limit(1)
    )
    if latest is None:
        raise HTTPException(409, "no OCR result for this session yet")
    doc = await paperless.get_document(s.entity_id)
    return OcrReviewOut(
        document_id=s.entity_id,
        previous_content=doc.content,
        ocr_text=latest.text,
        pages=len(latest.pages),
        timings=list(latest.timings or []),
    )


@router.post("/{session_id}/ocr/gate")
async def resolve_ocr_gate(
    session_id: int,
    body: OcrGateRequest,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> SessionOut:
    """Resolve the OCR gate: accept (possibly hand-fixed) content or keep
    the existing one; then the metadata analysis stage is scheduled."""
    s = await db.get(Session, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    if s.phase != SessionPhase.ocr_review:
        raise HTTPException(409, f"session is in phase {s.phase}, not ocr_review")
    latest = await db.scalar(
        select(OcrResult)
        .where(OcrResult.document_id == s.entity_id)
        .order_by(OcrResult.created_at.desc())
        .limit(1)
    )
    if latest is None:
        raise HTTPException(409, "no OCR result to accept")
    await pipeline.apply_ocr_gate(db, paperless, s, latest.text, body.content)
    await enqueue(
        db,
        "analysis",
        {"session_id": s.id},
        lane=QueueLane.interactive if s.job_id is None else QueueLane.batch,
        session_id=s.id,
        job_id=s.job_id,
    )
    return SessionOut.model_validate(s)


@router.post("/{session_id}/ocr/rerun")
async def rerun_ocr(
    session_id: int,
    body: OcrRerunRequest,
    db: AsyncSession = Depends(get_session),
) -> SessionOut:
    """Gate action: the user argues with the OCR. Re-runs it with their
    instructions in the OCR prompt and returns to the gate."""
    s = await db.get(Session, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    if s.phase != SessionPhase.ocr_review:
        raise HTTPException(409, f"session is in phase {s.phase}, not ocr_review")
    s.phase = SessionPhase.ocr_running
    if body.instructions:
        s.params = {**s.params, "ocr_instructions": body.instructions}
    await db.flush()
    await enqueue(
        db,
        "reocr",
        {"session_id": s.id, "instructions": body.instructions, "dpi": body.dpi},
        lane=QueueLane.interactive,
        session_id=s.id,
        job_id=s.job_id,
    )
    bus.publish(s.id, "phase_changed", phase=SessionPhase.ocr_running.value)
    return SessionOut.model_validate(s)


@router.post("/{session_id}/messages", status_code=202)
async def send_message(
    session_id: int,
    body: MessageRequest,
    db: AsyncSession = Depends(get_session),
) -> SessionOut:
    """Steer a session: append a user message and schedule one agent
    turn. Non-blocking — progress arrives via the SSE event stream."""
    s = await db.get(Session, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    if s.status == SessionStatus.running:
        raise HTTPException(409, "a turn is already running for this session")
    if s.phase in (SessionPhase.queued, SessionPhase.ocr_running, SessionPhase.ocr_review):
        raise HTTPException(
            409, f"session is in phase {s.phase}; steering starts after analysis"
        )
    if not body.content.strip():
        raise HTTPException(422, "empty message")
    # Flip to running in-request so the busy state is immediate and
    # concurrent sends 409 deterministically.
    s.status = SessionStatus.running
    s.error = None
    await db.flush()
    await enqueue(
        db,
        "steering",
        {"session_id": s.id, "content": body.content},
        lane=QueueLane.interactive,
        session_id=s.id,
    )
    bus.publish(s.id, "message_appended")
    return SessionOut.model_validate(s)


@router.post("/{session_id}/retry")
async def retry_session(
    session_id: int, db: AsyncSession = Depends(get_session)
) -> SessionOut:
    """Retry now: run a scheduled retry immediately, or revive an
    exhausted (failed) stage with a fresh attempt budget."""
    s = await db.get(Session, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    item = await db.scalar(
        select(QueueItem)
        .where(QueueItem.session_id == session_id)
        .order_by(QueueItem.id.desc())
        .limit(1)
    )
    if item is not None and item.state == QueueState.running:
        raise HTTPException(409, "stage is currently running")
    if item is not None and item.state == QueueState.pending:
        item.scheduled_at = None  # skip the backoff
        await db.commit()
    elif item is not None and item.state in (QueueState.failed, QueueState.cancelled):
        item.state = QueueState.pending
        item.attempts = 0  # fresh budget for a manual retry
        item.scheduled_at = None
        item.error = None
        item.finished_at = None
        await db.commit()
        if item.job_id is not None:
            from app.services.queue import _update_job

            await _update_job(db, item.job_id)
    elif s.status == SessionStatus.failed and s.phase in (
        SessionPhase.queued, SessionPhase.ocr_running, SessionPhase.analyzing
    ):
        # Orphan (no queue item, e.g. pre-queue session): re-enqueue by phase.
        stage = "analysis" if s.phase == SessionPhase.analyzing else "start"
        await enqueue(
            db, stage, {"session_id": s.id},
            lane=QueueLane.interactive, session_id=s.id, job_id=s.job_id,
        )
    else:
        raise HTTPException(409, "nothing to retry for this session")
    from app.services.queue import workers

    workers.wake(item.lane if item is not None else QueueLane.interactive)
    bus.publish(session_id, "retry_scheduled", attempts=0, max_attempts=0)
    return SessionOut.model_validate(s)


@router.get("/{session_id}/events")
async def session_events(
    session_id: int, db: AsyncSession = Depends(get_session)
) -> StreamingResponse:
    """SSE stream of session events. Events are invalidation signals —
    tiny JSON payloads; clients refetch state over the REST API."""
    s = await db.get(Session, session_id)
    if s is None:
        raise HTTPException(404, "session not found")

    async def gen():
        q = bus.subscribe(session_id)
        try:
            yield f"data: {json.dumps({'type': 'hello', 'session_id': session_id})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            bus.unsubscribe(session_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

