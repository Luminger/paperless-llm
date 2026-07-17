"""Document analysis pipeline.

    queued ──(redo_ocr)──> ocr_running ──> ocr_review  (GATE: user)
       │                                      │ accept / keep-existing
       └──────────────> analyzing <───────────┘
                            │
                          done

The OCR review is a hard gate: metadata analysis only runs after the
user accepted (possibly hand-fixed) or declined the new OCR text, and
it runs against whatever content paperless holds at that point.

Stages run as background tasks (single-process asyncio for now; the
celery lanes of M4 will take over scheduling — the stage functions are
already queue-agnostic).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runner import run_agent_turn
from app.config import get_settings
from app.db.models import (
    AgentKind,
    EntityType,
    Proposal,
    ProposalStatus,
    Session,
    SessionPhase,
    SessionStatus,
)
from app.db.session import session_scope
from app.llm.ocr import run_ocr
from app.paperless import PaperlessClient
from app.proposals.apply import apply_proposal
from app.proposals.schemas import ReplaceContent, dump_payload
from app.services.events import bus

log = logging.getLogger(__name__)


def _paperless_client() -> PaperlessClient:
    s = get_settings().paperless
    return PaperlessClient(
        s.base_url,
        s.token,
        timeout=s.timeout_seconds,
        username=s.username,
        password=s.password,
    )


async def _set_phase(db: AsyncSession, session: Session, phase: SessionPhase) -> None:
    session.phase = phase
    await db.commit()
    bus.publish(session.id, "phase_changed", phase=phase.value)


async def _mark_failed(db: AsyncSession, session: Session, exc: Exception) -> None:
    """Record a stage failure. The DB session may be poisoned by the very
    exception we're recording (e.g. IntegrityError mid-flush) — roll it
    back first, or the failure write itself fails and the session hangs
    in a running phase forever."""
    await db.rollback()
    session.status = SessionStatus.failed
    session.error = f"{type(exc).__name__}: {exc}"
    await db.commit()
    bus.publish(session.id, "failed", error=session.error)



async def run_stage_start(session_id: int) -> None:
    """Background entry: first pipeline stage after analyze()."""
    async with session_scope() as db:
        session = await db.get(Session, session_id)
        if session is None:
            return
        async with _paperless_client() as paperless:
            try:
                if session.params.get("redo_ocr"):
                    await _set_phase(db, session, SessionPhase.ocr_running)
                    await run_ocr(paperless, db, session.entity_id, force=True)
                    await _set_phase(db, session, SessionPhase.ocr_review)  # gate
                else:
                    await _run_analysis(db, paperless, session)
            except Exception as e:  # noqa: BLE001 — background boundary
                log.exception("pipeline stage failed for session %s", session_id)
                await _mark_failed(db, session, e)


async def run_stage_analysis(session_id: int) -> None:
    """Background entry: metadata analysis after the OCR gate."""
    async with session_scope() as db:
        session = await db.get(Session, session_id)
        if session is None:
            return
        async with _paperless_client() as paperless:
            try:
                await _run_analysis(db, paperless, session)
            except Exception as e:  # noqa: BLE001
                log.exception("analysis stage failed for session %s", session_id)
                await _mark_failed(db, session, e)


async def run_stage_reocr(
    session_id: int, instructions: str | None, dpi: int | None = None
) -> None:
    """Background entry: the user argued with the OCR at the gate — re-run
    it with their instructions folded into the OCR prompt, then return to
    the gate with a fresh diff."""
    async with session_scope() as db:
        session = await db.get(Session, session_id)
        if session is None:
            return
        async with _paperless_client() as paperless:
            try:
                await run_ocr(
                    paperless,
                    db,
                    session.entity_id,
                    force=True,
                    instructions=instructions,
                    dpi=dpi,
                )
                await _set_phase(db, session, SessionPhase.ocr_review)
            except Exception as e:  # noqa: BLE001
                log.exception("OCR re-run failed for session %s", session_id)
                await _mark_failed(db, session, e)


async def run_stage_steering(session_id: int, content: str) -> None:
    """Background entry: one steering turn (chat) on a finished session."""
    async with session_scope() as db:
        session = await db.get(Session, session_id)
        if session is None:
            return
        async with _paperless_client() as paperless:
            try:
                await run_agent_turn(paperless, db, session, content)
            except Exception as e:  # noqa: BLE001
                log.exception("steering turn failed for session %s", session_id)
                await _mark_failed(db, session, e)


def _kickoff_prompt(session: Session) -> str:
    """The synthetic first prompt of a pipeline session. Prefixes must
    stay in sync with transcript._PIPELINE_PROMPT_PREFIXES."""
    if session.agent_kind == AgentKind.document:
        return f"Process document id={session.entity_id}."
    noun = (session.entity_type or EntityType.tag).value.replace("_", " ")
    return f"Review {noun} id={session.entity_id}."


async def _run_analysis(db: AsyncSession, paperless: PaperlessClient, session: Session) -> None:
    await _set_phase(db, session, SessionPhase.analyzing)
    prompt = _kickoff_prompt(session)
    if session.params.get("ocr_gate") == "accepted":
        prompt += (
            "\nThe document's content was just re-OCRed and reviewed by the "
            "user - treat the stored content as accurate and do not "
            "second-guess it."
        )
    elif session.params.get("ocr_gate") == "kept_existing":
        prompt += (
            "\nThe user reviewed a re-OCR of this document and chose to keep "
            "the existing content."
        )
    if session.params.get("instructions"):
        prompt += f"\nAdditional instructions from the user: {session.params['instructions']}"
    await run_agent_turn(paperless, db, session, prompt)
    await _maybe_auto_apply(db, paperless, session)
    await _set_phase(db, session, SessionPhase.done)


async def _maybe_auto_apply(
    db: AsyncSession, paperless: PaperlessClient, session: Session
) -> None:
    """Campaign/webhook sessions may carry apply_policy=auto: apply the
    fresh proposals immediately — validated, journaled, revertible; the
    policy only skips the waiting. Failures leave the proposal pending
    for a human instead of failing the session."""
    if session.params.get("apply_policy") != "auto":
        return
    from sqlalchemy import select

    from app.proposals.apply import apply_proposal

    proposals = (
        await db.scalars(
            select(Proposal).where(
                Proposal.session_id == session.id,
                Proposal.status == ProposalStatus.pending,
            )
        )
    ).all()
    applied = 0
    for p in proposals:
        try:
            await apply_proposal(paperless, db, p)
            applied += 1
        except Exception:  # noqa: BLE001 — leave for human review
            log.exception("auto-apply failed for proposal %s", p.id)
    if applied:
        bus.publish(session.id, "proposals_applied", count=applied)


async def apply_ocr_gate(
    db: AsyncSession,
    paperless: PaperlessClient,
    session: Session,
    ocr_text: str,
    accepted_content: str | None,
) -> None:
    """Resolve the gate in-request (fast); the analysis stage is
    scheduled by the caller afterwards.

    ``accepted_content is None``  -> keep the existing paperless content.
    Otherwise write ``accepted_content`` (the user may have hand-fixed
    the OCR text) via an internal, journaled ReplaceContent proposal.
    """
    assert session.entity_id is not None
    # Leaving ocr_review here makes the gate single-shot (the route
    # refuses anything but ocr_review).
    session.phase = SessionPhase.analyzing
    bus.publish(session.id, "phase_changed", phase=SessionPhase.analyzing.value)
    if accepted_content is None:
        session.params = {**session.params, "ocr_gate": "kept_existing"}
        await db.commit()
        return

    doc = await paperless.get_document(session.entity_id)
    if accepted_content.strip() == doc.content.strip():
        # Nothing actually changes — no write, no journal noise.
        session.params = {**session.params, "ocr_gate": "accepted"}
        await db.commit()
        return

    agent_p = ReplaceContent(
        document_id=session.entity_id,
        content=ocr_text,
        reason="OCR gate: user-reviewed re-OCR of the document",
    )
    proposal = Proposal(
        session_id=session.id,
        kind=str(agent_p.kind),
        agent_payload=dump_payload(agent_p),
        user_payload=(
            dump_payload(
                ReplaceContent(
                    document_id=session.entity_id,
                    content=accepted_content,
                    reason="OCR gate: user-fixed re-OCR of the document",
                )
            )
            if accepted_content != ocr_text
            else None
        ),
        status=ProposalStatus.approved,
        entity_type=session.entity_type,
        entity_id=session.entity_id,
    )
    db.add(proposal)
    await db.flush()
    await apply_proposal(paperless, db, proposal)
    session.params = {**session.params, "ocr_gate": "accepted"}
    await db.commit()
