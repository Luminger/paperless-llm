"""The document pipeline: what steps DO. Executors and resolvers for
OCR, analysis, and chat turns, the auto-apply policy, and the decision
loop — registered into the step engine's registries at import.

The engine (``app.services.steps``) knows queueing, retries, and state;
this module knows documents, agents, proposals, and prompts."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from app.config import get_settings
from app.db.models import (
    Proposal,
    ProposalStatus,
    QueueLane,
    Session,
    Step,
    StepKind,
    StepState,
)
from app.llm.ocr import run_ocr
from app.paperless import PaperlessClient
from app.proposals.apply import apply_proposal
from app.proposals.kinds import is_internal, visible
from app.proposals.schemas import ReplaceContent, dump_payload
from app.services.steps import AWAIT_USER, EXECUTORS, RESOLVERS, create_step

log = logging.getLogger(__name__)


async def _exec_ocr(
    db: DbSession, paperless: PaperlessClient, session: Session, step: Step
) -> str | None:
    assert session.entity_id is not None
    # Resolve the EFFECTIVE dpi here so the record shows what actually
    # ran — the UI displays it even when it's just the default.
    dpi = step.input.get("dpi") or get_settings().llm.ocr.render_dpi
    outcome = await run_ocr(
        paperless,
        db,
        session.entity_id,
        force=True,
        instructions=step.input.get("instructions"),
        dpi=dpi,
    )
    step.result = {
        "pages": len(outcome.pages),
        "duration_s": round(sum(t.get("duration_s", 0) for t in outcome.timings or []), 1),
        "dpi": dpi,
        "from_cache": outcome.from_cache,
        # Snapshots so a later superseded rendering can still show what
        # THIS run produced and the diff it presented at the time.
        "text": outcome.text,
        "previous_content": outcome.previous_content,
    }
    # OCR-only + auto policy: no gate — the new text is written straight
    # away (journaled, revertible) and the pipeline ends here.
    if session.params.get("ocr_only") and session.params.get("apply_policy") == "auto":
        text = (outcome.text or "").strip()
        prev = (outcome.previous_content or "").strip()
        if text and text != prev:
            proposal = Proposal(
                session_id=session.id,
                step_id=step.id,
                kind="replace_content",
                agent_payload=dump_payload(
                    ReplaceContent(document_id=session.entity_id, content=outcome.text)
                ),
                status=ProposalStatus.pending,
                entity_type=session.entity_type,
                entity_id=session.entity_id,
            )
            db.add(proposal)
            await db.flush()
            await apply_proposal(paperless, db, proposal)
            resolution = "auto_applied"
        else:
            resolution = "unchanged"
        step.result = {**step.result, "resolution": resolution, "edited": False}
        session.params = {**session.params, "ocr_gate": resolution}
        return None
    return AWAIT_USER  # the gate: user reviews the diff


def _kickoff_prompt(session: Session, step: Step) -> str:
    if session.agent_kind.value == "document":
        prompt = f"Process document id={session.entity_id}."
    else:
        noun = (session.entity_type.value if session.entity_type else "entity").replace("_", " ")
        prompt = f"Review {noun} id={session.entity_id}."
    gate = step.input.get("gate")
    if gate == "accepted":
        prompt += (
            "\nThe document's content was just re-OCRed and reviewed by the "
            "user - treat the stored content as accurate and do not "
            "second-guess it."
        )
    elif gate == "kept_existing":
        prompt += (
            "\nThe user reviewed a re-OCR of this document and chose to keep "
            "the existing content."
        )
    instructions = step.input.get("instructions") or session.params.get("instructions")
    if instructions:
        prompt += f"\nAdditional instructions from the user: {instructions}"
    return prompt


async def _run_turn(
    db: DbSession, paperless: PaperlessClient, session: Session, step: Step, prompt: str
) -> None:
    from app.agents.runner import run_agent_turn

    outcome = await run_agent_turn(paperless, db, session, prompt, step=step)
    step.result = {
        "message_range": list(outcome.message_range),
        "proposal_ids": outcome.proposal_ids,
    }
    await _maybe_auto_apply(db, paperless, session, step)


async def _exec_analysis(
    db: DbSession, paperless: PaperlessClient, session: Session, step: Step
) -> str | None:
    await _run_turn(db, paperless, session, step, _kickoff_prompt(session, step))
    return None


async def _exec_chat(
    db: DbSession, paperless: PaperlessClient, session: Session, step: Step
) -> str | None:
    await _run_turn(db, paperless, session, step, step.input["content"])
    return None


async def _maybe_auto_apply(
    db: DbSession, paperless: PaperlessClient, session: Session, step: Step
) -> None:
    """apply_policy=auto (bulk jobs/webhook): apply fresh proposals right
    away — validated, journaled, revertible. Failures stay pending for a
    human instead of failing the step. Under the decision loop this
    auto-continues the session (bounded), so autonomous runs converge."""
    if session.params.get("apply_policy") != "auto":
        return
    # AUDIT SV-M5: the user may have archived the session while this
    # turn ran — archived means "refuse forward-apply", same as the
    # human path enforces. Re-read from the DB: the ORM object was
    # loaded at turn START (expire_on_commit=False), so the cached
    # attribute misses an archive that landed during the LLM-minutes
    # the turn took (reinspection finding).
    archived = await db.scalar(
        select(Session.archived_at).where(Session.id == session.id)
    )
    if archived is not None:
        return
    proposals = (
        await db.scalars(
            select(Proposal).where(
                Proposal.step_id == step.id, Proposal.status == ProposalStatus.pending
            )
        )
    ).all()
    for p in proposals:
        try:
            await apply_proposal(paperless, db, p)
        except Exception:  # noqa: BLE001
            log.exception("auto-apply failed for proposal %s", p.id)
            continue
        # AUDIT SV-H1: this runs while the triggering step is still
        # committed as 'running' — without the exclusion the busy check
        # always refuses and autonomous runs stop after one change.
        await continue_after_decision(db, session, p, exclude_step_id=step.id)


# ----- the decision loop ----------------------------------------------

def _decision_message(p: Proposal) -> str:
    """Synthetic pipeline prompt telling the agent what the user did.
    Hidden in the UI (the timeline already shows the decision); the
    model needs it to continue the loop."""
    kind = str(p.kind).replace("_", " ")
    if p.status == ProposalStatus.no_change:
        head = (
            f"Paperless already matched your proposal ({kind}) — nothing "
            "was written."
        )
    elif p.user_payload is not None:
        head = (
            f"The user edited your proposal ({kind}) before applying it. "
            f"The APPLIED values are: {json.dumps(p.user_payload, ensure_ascii=False)}. "
            "These are the user's preference and override yours."
        )
    else:
        head = f"The user accepted your proposal ({kind}) as-is; it has been applied."
    return (
        f"{head} Continue the review: if further changes are needed, "
        "propose the SINGLE next one; otherwise finish with a brief "
        "closing summary."
    )


async def continue_after_decision(
    db: DbSession,
    session: Session,
    proposal: Proposal,
    exclude_step_id: int | None = None,
) -> Step | None:
    """After the user (or the auto policy) decided a proposal, the
    session continues on its own: a new turn tells the agent what
    happened. Skipped when the session is archived, other proposals
    are still open, work is already in flight, or the auto brake hit.

    ``exclude_step_id``: the auto-apply path calls this from INSIDE the
    executor of the step that produced the proposal — that step is
    still 'running' and must not count as in-flight work."""
    # Fresh read, not the cached attribute — see the SV-M5 note in
    # _maybe_auto_apply (the executor path passes a stale ORM object).
    archived = await db.scalar(
        select(Session.archived_at).where(Session.id == session.id)
    )
    if archived is not None:
        return None
    if proposal.step_id is None or is_internal(proposal.kind):
        return None
    if proposal.status not in (ProposalStatus.applied, ProposalStatus.no_change):
        return None
    open_left = await db.scalar(
        select(func.count()).select_from(Proposal).where(
            Proposal.session_id == session.id,
            Proposal.status == ProposalStatus.pending,
            visible(),
        )
    )
    if open_left:
        return None  # legacy multi-proposal turns: the user decides each
    busy_q = select(func.count()).select_from(Step).where(
        Step.session_id == session.id,
        Step.state.in_(
            (StepState.pending, StepState.running, StepState.awaiting_user)
        ),
    )
    if exclude_step_id is not None:
        busy_q = busy_q.where(Step.id != exclude_step_id)
    busy = await db.scalar(busy_q)
    if busy:
        return None
    if session.params.get("apply_policy") == "auto":
        limit = get_settings().queue.auto_continuation_limit
        # Counted in Python: JSON-path predicates differ across
        # dialects, and a session has few chat steps.
        chat_steps = (
            await db.scalars(
                select(Step).where(
                    Step.session_id == session.id, Step.kind == StepKind.chat
                )
            )
        ).all()
        auto_turns = sum(1 for s in chat_steps if s.input.get("auto"))
        if auto_turns >= limit:
            log.warning(
                "session %s hit the auto-continuation limit (%s)",
                session.id, limit,
            )
            return None
    step_row = await db.get(Step, proposal.step_id)
    lane = step_row.lane if step_row is not None else QueueLane.interactive
    return await create_step(
        db,
        session,
        StepKind.chat,
        {"content": _decision_message(proposal), "auto": True},
        lane=lane,
    )


async def _resolve_ocr(
    db: DbSession,
    paperless: PaperlessClient,
    session: Session,
    step: Step,
    body: dict[str, Any],
) -> Step | None:
    """The OCR gate. body: {"content": str|None} — None keeps the
    existing paperless content; a string is the accepted (possibly
    hand-fixed) text, written via an internal journaled proposal. The
    analysis step is created either way."""
    assert session.entity_id is not None
    accepted = body.get("content")
    if accepted is None:
        resolution = "kept_existing"
    else:
        doc = await paperless.get_document(session.entity_id)
        resolution = "accepted"
        if accepted.strip() != doc.content.strip():
            from app.db.models import OcrResult

            latest = await db.scalar(
                select(OcrResult)
                .where(OcrResult.document_id == session.entity_id)
                .order_by(OcrResult.created_at.desc())
                .limit(1)
            )
            ocr_text = latest.text if latest else accepted
            agent_p = ReplaceContent(
                document_id=session.entity_id,
                content=ocr_text,
            )
            proposal = Proposal(
                session_id=session.id,
                step_id=step.id,
                kind=str(agent_p.kind),
                agent_payload=dump_payload(agent_p),
                user_payload=(
                    dump_payload(
                        ReplaceContent(
                            document_id=session.entity_id,
                            content=accepted,
                        )
                    )
                    if accepted != ocr_text
                    else None
                ),
                status=ProposalStatus.pending,
                entity_type=session.entity_type,
                entity_id=session.entity_id,
            )
            db.add(proposal)
            await db.flush()
            await apply_proposal(paperless, db, proposal)
    # "edited" means the user changed the OCR text before accepting it.
    edited = accepted is not None and accepted.strip() != str(
        step.result.get("text") or ""
    ).strip()
    step.result = {**step.result, "resolution": resolution, "edited": edited}
    session.params = {**session.params, "ocr_gate": resolution}
    if session.params.get("ocr_only"):
        return None  # the pipeline ENDS at the gate — no analysis follows
    analysis_input: dict[str, Any] = {"gate": resolution}
    if session.params.get("instructions"):
        analysis_input["instructions"] = session.params["instructions"]
    # AUDIT SV-H3: committed by resolve_step in the SAME transaction
    # that marks the gate succeeded — never wake workers for an
    # analysis step while the gate is still resolvable.
    return await create_step(
        db, session, StepKind.analysis, analysis_input, lane=step.lane,
        commit=False,
    )


EXECUTORS.update(
    {
        StepKind.ocr: _exec_ocr,
        StepKind.analysis: _exec_analysis,
        StepKind.chat: _exec_chat,
    }
)
RESOLVERS[StepKind.ocr] = _resolve_ocr


