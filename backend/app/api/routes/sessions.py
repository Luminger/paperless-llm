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
    AnalyzeRequest,
    MessageRequest,
    OcrGateRequest,
    OcrRerunRequest,
    OcrReviewOut,
    SessionDetailOut,
    SessionOut,
)
from app.db.models import (
    AgentKind,
    EntityType,
    OcrResult,
    Proposal,
    Session,
    SessionPhase,
    SessionStatus,
)
from app.db.session import get_session
from app.paperless import PaperlessClient
from app.services import pipeline
from app.services.events import bus
from app.services.transcript import derive_transcript

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# Strong references: the event loop only keeps weak refs to tasks — an
# unreferenced pipeline stage can be garbage-collected mid-flight.
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Schedule a pipeline stage. Single-process asyncio for now; the
    M4 celery lanes replace this. Tests monkeypatch it."""
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


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
    await db.commit()
    _spawn(pipeline.run_stage_start(s.id))
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
    _spawn(pipeline.run_stage_analysis(s.id))
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
    await db.commit()
    bus.publish(s.id, "phase_changed", phase=SessionPhase.ocr_running.value)
    _spawn(pipeline.run_stage_reocr(s.id, body.instructions, body.dpi))
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
    await db.commit()
    bus.publish(s.id, "message_appended")
    _spawn(pipeline.run_stage_steering(s.id, body.content))
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

