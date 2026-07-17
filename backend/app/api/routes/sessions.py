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
    OcrReviewOut,
    RedoRequest,
    ResolveRequest,
    SessionDetailOut,
    SessionOut,
    SessionPage,
    StepOut,
)
from app.db.models import (
    AgentKind,
    EntityType,
    OcrResult,
    Proposal,
    ProposalStatus,
    Session,
    SessionPhase,
    SessionStatus,
    Step,
    StepKind,
    StepState,
)
from app.db.session import get_session
from app.paperless import PaperlessClient
from app.services import steps as engine
from app.services.events import bus
from app.services.transcript import derive_transcript

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(
    entity_type: str | None = None,
    entity_id: int | None = None,
    archived: bool = False,
    unfinished: bool = False,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_session),
) -> SessionPage:
    """Paginated session list, filterable by bound entity. Active and
    archived sessions are separate lists (archived=true for the
    latter); unfinished=true keeps only sessions that still need
    something (gates, running/queued work, failures — or proposals
    still waiting for review)."""
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    where = [
        Session.archived_at.is_not(None) if archived else Session.archived_at.is_(None)
    ]
    if unfinished:
        # A finished analysis whose proposals await review still needs
        # the user — it stays on the dashboard until decided.
        has_open_proposal = (
            select(Proposal.id)
            .where(
                Proposal.session_id == Session.id,
                Proposal.status == ProposalStatus.pending,
                Proposal.kind != "replace_content",
            )
            # The outer list query also joins Proposal — correlate only
            # Session so the subquery keeps its own FROM.
            .correlate(Session)
            .exists()
        )
        where.append(
            (Session.phase.is_(None))
            | (Session.phase != SessionPhase.done)
            | (Session.status != SessionStatus.idle)
            | has_open_proposal
        )
    if entity_type is not None:
        try:
            where.append(Session.entity_type == EntityType(entity_type))
        except ValueError as e:
            raise HTTPException(422, f"unknown entity type {entity_type!r}") from e
    if entity_id is not None:
        where.append(Session.entity_id == entity_id)
    count = await db.scalar(select(func.count()).select_from(Session).where(*where)) or 0
    q = (
        select(Session, func.count(Proposal.id))
        .outerjoin(Proposal, Proposal.session_id == Session.id)
        .where(*where)
        .group_by(Session.id)
        .order_by(Session.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    results: list[SessionOut] = []
    for s, n in (await db.execute(q)).all():
        item = SessionOut.model_validate(s)
        item.proposal_count = n
        results.append(item)
    return SessionPage(count=count, page=page, page_size=page_size, results=results)


def _step_out(step: Step, history: list) -> StepOut:
    out = StepOut.model_validate(step)
    rng = step.result.get("message_range")
    if rng and isinstance(rng, list) and len(rng) == 2:
        out.transcript = derive_transcript(history[rng[0] : rng[1]])
    return out


@router.get("/{session_id}")
async def get_session_detail(
    session_id: int, db: AsyncSession = Depends(get_session)
) -> SessionDetailOut:
    s = await db.get(Session, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    # Build from the base schema: SessionDetailOut fields would otherwise
    # collide with same-named (lazy) ORM relationships.
    out = SessionDetailOut(**SessionOut.model_validate(s).model_dump())
    history = s.message_history or []
    step_rows = (
        await db.scalars(
            select(Step).where(Step.session_id == s.id).order_by(Step.id)
        )
    ).all()
    out.steps = [_step_out(step, history) for step in step_rows]
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
    """Start a document analysis: a session whose first step is either
    the OCR (gated) or the analysis itself."""
    body = body or AnalyzeRequest()
    s = Session(
        agent_kind=AgentKind.document,
        entity_type=EntityType.document,
        entity_id=document_id,
        params={
            "redo_ocr": body.redo_ocr,
            **({"instructions": body.instructions} if body.instructions else {}),
        },
        title=f"Document #{document_id} analysis",
    )
    db.add(s)
    await db.flush()
    await engine.create_step(
        db, s, StepKind.ocr if body.redo_ocr else StepKind.analysis
    )
    return SessionOut.model_validate(s)


@router.post("/analyze/{entity_type}/{entity_id}")
async def analyze_entity(
    entity_type: str,
    entity_id: int,
    body: AnalyzeEntityRequest | None = None,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> SessionOut:
    """Start a taxonomy review session (tag/correspondent/document_type).
    The inbox tag is a workflow marker, not a label — analyzing it for
    applicability is meaningless and refused."""
    if entity_type not in ("tag", "correspondent", "document_type"):
        raise HTTPException(422, f"cannot analyze entity type {entity_type!r}")
    if entity_type == "tag":
        tag = await paperless.get_tag(entity_id)
        if tag.is_inbox_tag:
            raise HTTPException(
                422, "the inbox tag is a workflow marker and cannot be analyzed"
            )
    body = body or AnalyzeEntityRequest()
    s = Session(
        agent_kind=AgentKind(entity_type),
        entity_type=EntityType(entity_type),
        entity_id=entity_id,
        params={"instructions": body.instructions} if body.instructions else {},
        title=f"{entity_type.replace('_', ' ')} #{entity_id} review",
    )
    db.add(s)
    await db.flush()
    await engine.create_step(db, s, StepKind.analysis)
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


async def _require_not_archived(db: AsyncSession, session_id: int) -> Session:
    s = await db.get(Session, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    if s.archived_at is not None:
        raise HTTPException(
            409, "session is archived; unarchive it to continue working with it"
        )
    return s


async def _load_step(db: AsyncSession, session_id: int, step_id: int) -> Step:
    step = await db.get(Step, step_id)
    if step is None or step.session_id != session_id:
        raise HTTPException(404, "step not found")
    await _require_not_archived(db, session_id)
    return step


@router.post("/{session_id}/archive")
async def archive_session(
    session_id: int, db: AsyncSession = Depends(get_session)
) -> SessionOut:
    """Archive: leaves the active lists, refuses forward-apply and new
    steps. The journal keeps working — applied changes stay revertible
    (you can always go BACK to a state, just not forward-apply)."""
    s = await db.get(Session, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    from app.db.models import utcnow

    if s.archived_at is None:
        s.archived_at = utcnow()
        from app.services.audit import record

        await record(db, "session", "archived", session_id=s.id, title=s.title)
        await db.commit()
    return SessionOut.model_validate(s)


@router.post("/{session_id}/unarchive")
async def unarchive_session(
    session_id: int, db: AsyncSession = Depends(get_session)
) -> SessionOut:
    s = await db.get(Session, session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    if s.archived_at is not None:
        s.archived_at = None
        from app.services.audit import record

        await record(db, "session", "unarchived", session_id=s.id, title=s.title)
    await db.commit()
    return SessionOut.model_validate(s)


@router.post("/{session_id}/steps/{step_id}/resolve")
async def resolve_step(
    session_id: int,
    step_id: int,
    body: ResolveRequest,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> StepOut:
    """Resolve an awaiting_user step (the OCR gate: content=None keeps
    the existing text, a string is the accepted/hand-fixed version)."""
    step = await _load_step(db, session_id, step_id)
    try:
        await engine.resolve_step(db, paperless, step, body.model_dump())
    except engine.StepActionError as e:
        raise HTTPException(409, str(e)) from e
    return StepOut.model_validate(step)


@router.post("/{session_id}/steps/{step_id}/retry")
async def retry_step(
    session_id: int, step_id: int, db: AsyncSession = Depends(get_session)
) -> StepOut:
    """Generic retry-now: skip a scheduled backoff or revive a failed
    step with a fresh auto-retry budget. Never limited."""
    step = await _load_step(db, session_id, step_id)
    try:
        await engine.retry_step(db, step)
    except engine.StepActionError as e:
        raise HTTPException(409, str(e)) from e
    return StepOut.model_validate(step)


@router.post("/{session_id}/steps/{step_id}/redo")
async def redo_step(
    session_id: int,
    step_id: int,
    body: RedoRequest | None = None,
    db: AsyncSession = Depends(get_session),
) -> StepOut:
    """Generic redo: supersede the step and run a fresh one, optionally
    with amended input (e.g. OCR re-run with instructions)."""
    step = await _load_step(db, session_id, step_id)
    try:
        new = await engine.redo_step(db, step, (body.input if body else None) or None)
    except engine.StepActionError as e:
        raise HTTPException(409, str(e)) from e
    return StepOut.model_validate(new)


@router.post("/{session_id}/messages", status_code=202)
async def send_message(
    session_id: int,
    body: MessageRequest,
    db: AsyncSession = Depends(get_session),
) -> StepOut:
    """Steer a session: append a chat step. Non-blocking — progress
    arrives via the SSE stream."""
    s = await _require_not_archived(db, session_id)
    if not body.content.strip():
        raise HTTPException(422, "empty message")
    blocked = await db.scalar(
        select(Step).where(
            Step.session_id == session_id,
            Step.state.in_(
                [StepState.pending, StepState.running, StepState.awaiting_user]
            ),
        )
    )
    if blocked is not None:
        raise HTTPException(
            409,
            f"a {blocked.kind.value} step is {blocked.state.value}; "
            "wait for it (or resolve it) before steering",
        )
    if s.phase in (SessionPhase.queued, SessionPhase.ocr_running, SessionPhase.ocr_review):
        raise HTTPException(409, f"session is in phase {s.phase}; steering starts after analysis")
    step = await engine.create_step(
        db, s, StepKind.chat, {"content": body.content}
    )
    return StepOut.model_validate(step)


@router.get("/{session_id}/events")
async def session_events(
    session_id: int, db: AsyncSession = Depends(get_session)
) -> StreamingResponse:
    """SSE stream: step_changed (invalidation signal) + step_progress
    (live tokens/tools). Tiny payloads; clients refetch over REST."""
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
