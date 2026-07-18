"""The step engine: sessions are ordered lists of Steps; this module is
the ONLY writer of step state, attempt history, retry scheduling, and
the session's derived phase/status.

- Executors (kind -> coroutine) do the work and know nothing about
  queueing, retries, or state: they fill ``step.result`` and either
  return normally (success), return AWAIT_USER (pause for input), or
  raise (failure).
- Workers claim pending steps by lane (the step IS the queue item).
- Generic actions apply to every kind: ``retry_step`` (failed -> run
  again, attempts append), ``redo_step`` (terminal/awaiting -> new step
  with optionally changed input, old one superseded), ``resolve_step``
  (awaiting_user -> kind-specific resolution, e.g. the OCR gate).
- Failure policy: auto-retries per config with delay; manual retries
  reset the auto budget and are never limited.

SSE: two event types only — ``step_changed`` {step_id, state} as the
invalidation signal and ``step_progress`` {step_id, ...} for live
tokens/tool calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from app.config import get_settings
from app.db.models import (
    Job,
    JobStatus,
    Proposal,
    ProposalStatus,
    QueueLane,
    Session,
    SessionPhase,
    SessionStatus,
    Step,
    StepKind,
    StepState,
    utcnow,
)
from app.db.session import session_scope
from app.llm.ocr import run_ocr
from app.paperless import PaperlessClient
from app.proposals.apply import apply_proposal
from app.proposals.schemas import ReplaceContent, dump_payload
from app.services.audit import record as audit_record
from app.services.events import bus

log = logging.getLogger(__name__)

AWAIT_USER = "awaiting_user"

TERMINAL = (StepState.succeeded, StepState.failed, StepState.superseded, StepState.cancelled)


def _paperless_client() -> PaperlessClient:
    s = get_settings().paperless
    return PaperlessClient(
        s.base_url, s.token,
        timeout=s.timeout_seconds, username=s.username, password=s.password,
    )


def _publish(step: Step) -> None:
    bus.publish(
        step.session_id, "step_changed", step_id=step.id, state=step.state.value
    )


# ----- session phase/status derivation (single writer) ----------------


def _derive(steps: list[Step]) -> tuple[SessionPhase | None, SessionStatus, str | None]:
    """Phase/status/error from the step list (chronological)."""
    live = [s for s in steps if s.state != StepState.superseded]
    if not live:
        return None, SessionStatus.idle, None
    last = live[-1]
    pipeline = [s for s in live if s.kind in (StepKind.ocr, StepKind.analysis)]
    phase: SessionPhase | None = None
    if pipeline:
        p = pipeline[-1]
        if p.kind == StepKind.ocr:
            phase = {
                StepState.pending: SessionPhase.queued,
                StepState.running: SessionPhase.ocr_running,
                StepState.awaiting_user: SessionPhase.ocr_review,
                StepState.failed: SessionPhase.ocr_running,
                StepState.succeeded: SessionPhase.ocr_review,
                StepState.cancelled: SessionPhase.ocr_running,
            }[p.state]
            # A succeeded+resolved gate means analysis follows/finished.
            if p.state == StepState.succeeded and p.result.get("resolution"):
                phase = SessionPhase.analyzing
        else:
            phase = {
                StepState.pending: SessionPhase.queued,
                StepState.running: SessionPhase.analyzing,
                StepState.awaiting_user: SessionPhase.analyzing,
                StepState.failed: SessionPhase.analyzing,
                StepState.succeeded: SessionPhase.done,
                StepState.cancelled: SessionPhase.analyzing,
            }[p.state]
    status = (
        SessionStatus.running
        if any(s.state == StepState.running for s in live)
        else SessionStatus.failed
        if last.state == StepState.failed
        else SessionStatus.idle
    )
    return phase, status, (last.error if last.state == StepState.failed else None)


async def sync_session(db: DbSession, session: Session) -> None:
    steps = (
        await db.scalars(
            select(Step).where(Step.session_id == session.id).order_by(Step.id)
        )
    ).all()
    phase, status, error = _derive(list(steps))
    session.phase = phase
    session.status = status
    session.error = error


# ----- creation & generic actions -------------------------------------


async def create_step(
    db: DbSession,
    session: Session,
    kind: StepKind,
    input: dict[str, Any] | None = None,
    *,
    lane: QueueLane | None = None,
    supersedes_id: int | None = None,
) -> Step:
    step = Step(
        session_id=session.id,
        kind=kind,
        input=input or {},
        lane=lane
        or (QueueLane.batch if session.job_id is not None else QueueLane.interactive),
        max_attempts=1 + max(0, get_settings().queue.retry_attempts),
        supersedes_id=supersedes_id,
    )
    db.add(step)
    await db.flush()
    # Scheduling is a data operation — it shows up in the audit log.
    await audit_record(
        db, "task", "scheduled",
        step_id=step.id, step_kind=str(kind.value),
        session_id=session.id, lane=str(step.lane.value),
    )
    await sync_session(db, session)
    await db.commit()
    _publish(step)
    workers.wake(step.lane)
    return step


class StepActionError(Exception):
    """Invalid action for the step's current state (HTTP 409)."""


async def retry_step(db: DbSession, step: Step) -> Step:
    """Run a failed (or backoff-scheduled) step again, now. Manual —
    resets the auto-retry budget, never limited."""
    if step.state == StepState.pending and step.scheduled_at is not None:
        step.scheduled_at = None  # skip the backoff
    elif step.state in (StepState.failed, StepState.cancelled):
        step.state = StepState.pending
        step.attempt_count = 0  # fresh auto budget after a manual retry
        step.scheduled_at = None
        step.attempts = [*step.attempts, {"manual_retry_at": utcnow().isoformat()}]
    else:
        raise StepActionError(f"step is {step.state.value}; nothing to retry")
    await audit_record(
        db, "task", "retry_requested",
        step_id=step.id, step_kind=str(step.kind.value),
        session_id=step.session_id,
    )
    session = await db.get(Session, step.session_id)
    if session is not None:
        await sync_session(db, session)
    await db.commit()
    _publish(step)
    workers.wake(step.lane)
    return step


async def redo_step(
    db: DbSession, step: Step, input_override: dict[str, Any] | None = None
) -> Step:
    """Do a step over — generic for every kind: supersedes the old step
    AND every step after it (their results were built on state this redo
    invalidates), then creates a fresh step with (optionally amended)
    input. Open proposals of the superseded steps are superseded too;
    applied ones are history and stay untouched."""
    if step.state not in (*TERMINAL, StepState.awaiting_user):
        raise StepActionError(f"step is {step.state.value}; wait for it to finish")
    session = await db.get(Session, step.session_id)
    assert session is not None

    later = (
        await db.scalars(
            select(Step)
            .where(Step.session_id == session.id, Step.id > step.id)
            .order_by(Step.id)
        )
    ).all()
    if any(s.state in (StepState.pending, StepState.running) for s in later):
        raise StepActionError(
            "steps after this one are still queued or running; wait for them first"
        )

    to_supersede = [step] + [
        s
        for s in later
        if s.state in (StepState.succeeded, StepState.failed, StepState.awaiting_user)
    ]
    for s in to_supersede:
        s.state = StepState.superseded
        _publish(s)
    open_proposals = (
        await db.scalars(
            select(Proposal).where(
                Proposal.step_id.in_([s.id for s in to_supersede]),
                Proposal.status.in_(
                    [ProposalStatus.draft, ProposalStatus.pending]
                ),
            )
        )
    ).all()
    for p in open_proposals:
        p.status = ProposalStatus.superseded

    await audit_record(
        db, "task", "redone",
        step_id=step.id, step_kind=str(step.kind.value),
        session_id=session.id,
        superseded_steps=[s.id for s in to_supersede],
        superseded_proposals=[p.id for p in open_proposals],
    )
    return await create_step(
        db,
        session,
        step.kind,
        {**step.input, **(input_override or {})},
        lane=step.lane,
        supersedes_id=step.id,
    )


async def resolve_step(
    db: DbSession, paperless: PaperlessClient, step: Step, body: dict[str, Any]
) -> Step:
    """Resolve an awaiting_user step (kind-specific semantics)."""
    if step.state != StepState.awaiting_user:
        raise StepActionError(f"step is {step.state.value}, not awaiting user input")
    resolver = RESOLVERS.get(step.kind)
    if resolver is None:
        raise StepActionError(f"steps of kind {step.kind.value} are not resolvable")
    session = await db.get(Session, step.session_id)
    assert session is not None
    await resolver(db, paperless, session, step, body)
    step.state = StepState.succeeded
    step.finished_at = utcnow()
    await sync_session(db, session)
    await db.commit()
    _publish(step)
    return step


# ----- executors ------------------------------------------------------


async def _exec_ocr(
    db: DbSession, paperless: PaperlessClient, session: Session, step: Step
) -> str | None:
    assert session.entity_id is not None
    outcome = await run_ocr(
        paperless,
        db,
        session.entity_id,
        force=True,
        instructions=step.input.get("instructions"),
        dpi=step.input.get("dpi"),
    )
    step.result = {
        "pages": len(outcome.pages),
        "duration_s": round(sum(t.get("duration_s", 0) for t in outcome.timings or []), 1),
        "from_cache": outcome.from_cache,
        # Snapshots so a later superseded rendering can still show what
        # THIS run produced and the diff it presented at the time.
        "text": outcome.text,
        "previous_content": outcome.previous_content,
    }
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
    if session.params.get("instructions"):
        prompt += f"\nAdditional instructions from the user: {session.params['instructions']}"
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


EXECUTORS = {
    StepKind.ocr: _exec_ocr,
    StepKind.analysis: _exec_analysis,
    StepKind.chat: _exec_chat,
}


async def _maybe_auto_apply(
    db: DbSession, paperless: PaperlessClient, session: Session, step: Step
) -> None:
    """apply_policy=auto (bulk jobs/webhook): apply fresh proposals right
    away — validated, journaled, revertible. Failures stay pending for a
    human instead of failing the step. Under the decision loop this
    auto-continues the session (bounded), so autonomous runs converge."""
    if session.params.get("apply_policy") != "auto":
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
        await continue_after_decision(db, session, p)


# ----- the decision loop ----------------------------------------------

# Runaway brake for autonomous (auto-apply) sessions: at most this many
# auto-continuation turns per session. Manual continuations (user
# applies) are driven by the user and never limited.
CONTINUATION_LIMIT = 10


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
    db: DbSession, session: Session, proposal: Proposal
) -> Step | None:
    """After the user (or the auto policy) decided a proposal, the
    session continues on its own: a new turn tells the agent what
    happened. Skipped when the session is archived, other proposals
    are still open, work is already in flight, or the auto brake hit."""
    if session.archived_at is not None:
        return None
    if proposal.step_id is None or str(proposal.kind) == "replace_content":
        return None
    if proposal.status not in (ProposalStatus.applied, ProposalStatus.no_change):
        return None
    open_left = await db.scalar(
        select(func.count()).select_from(Proposal).where(
            Proposal.session_id == session.id,
            Proposal.status == ProposalStatus.pending,
            Proposal.kind != "replace_content",
        )
    )
    if open_left:
        return None  # legacy multi-proposal turns: the user decides each
    busy = await db.scalar(
        select(func.count()).select_from(Step).where(
            Step.session_id == session.id,
            Step.state.in_(
                (StepState.pending, StepState.running, StepState.awaiting_user)
            ),
        )
    )
    if busy:
        return None
    if session.params.get("apply_policy") == "auto":
        auto_turns = await db.scalar(
            select(func.count()).select_from(Step).where(
                Step.session_id == session.id,
                Step.kind == StepKind.chat,
                Step.input["auto"].as_boolean() == True,  # noqa: E712
            )
        )
        if (auto_turns or 0) >= CONTINUATION_LIMIT:
            log.warning(
                "session %s hit the auto-continuation limit (%s)",
                session.id, CONTINUATION_LIMIT,
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


# ----- resolvers (awaiting_user) --------------------------------------


async def _resolve_ocr(
    db: DbSession,
    paperless: PaperlessClient,
    session: Session,
    step: Step,
    body: dict[str, Any],
) -> None:
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
    step.result = {**step.result, "resolution": resolution, "edited": bool(
        accepted is not None and step.result.get("resolution") is None and accepted
    )}
    session.params = {**session.params, "ocr_gate": resolution}
    await create_step(db, session, StepKind.analysis, {"gate": resolution}, lane=step.lane)


RESOLVERS = {StepKind.ocr: _resolve_ocr}


# ----- worker pool ----------------------------------------------------


class StepWorkers:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._wakeups: dict[QueueLane, asyncio.Event] = {}
        self._claim_lock = asyncio.Lock()

    def wake(self, lane: QueueLane) -> None:
        ev = self._wakeups.get(lane)
        if ev is not None:
            ev.set()

    async def start(self) -> None:
        cfg = get_settings().queue
        for lane, n in (
            (QueueLane.interactive, cfg.interactive_concurrency),
            (QueueLane.batch, cfg.batch_concurrency),
        ):
            self._wakeups[lane] = asyncio.Event()
            for _ in range(max(1, n)):
                self._tasks.append(asyncio.create_task(self._worker(lane)))
        log.info("step workers started (%d tasks)", len(self._tasks))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._wakeups.clear()

    async def _worker(self, lane: QueueLane) -> None:
        poll = get_settings().queue.poll_interval_seconds
        while True:
            try:
                step_id = await self._claim(lane)
                if step_id is None:
                    ev = self._wakeups[lane]
                    try:
                        await asyncio.wait_for(ev.wait(), timeout=poll)
                    except TimeoutError:
                        pass
                    ev.clear()
                    continue
                await self._run(step_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — worker must survive anything
                log.exception("step worker error (lane=%s)", lane)
                await asyncio.sleep(1)

    async def _claim(self, lane: QueueLane) -> int | None:
        async with self._claim_lock:
            async with session_scope() as db:
                step = await db.scalar(
                    select(Step)
                    .where(
                        Step.state == StepState.pending,
                        Step.lane == lane,
                        (Step.scheduled_at.is_(None)) | (Step.scheduled_at <= utcnow()),
                    )
                    .order_by(Step.id)
                    .limit(1)
                )
                if step is None:
                    return None
                step.state = StepState.running
                step.attempt_count += 1
                step.started_at = step.started_at or utcnow()
                session = await db.get(Session, step.session_id)
                if session is not None:
                    await sync_session(db, session)
                await db.commit()
                _publish(step)
                return step.id

    async def _run(self, step_id: int) -> None:
        attempt_started = utcnow()
        async with session_scope() as db:
            step = await db.get(Step, step_id)
            if step is None:
                return
            kind, attempt_no = step.kind, step.attempt_count

        error: str | None = None
        verdict: str | None = None
        try:
            async with session_scope() as db:
                step = await db.get(Step, step_id)
                session = await db.get(Session, step.session_id)
                async with _paperless_client() as paperless:
                    verdict = await EXECUTORS[kind](db, paperless, session, step)
                    await db.commit()
        except Exception as e:  # noqa: BLE001 — failure boundary
            log.exception("step %s (%s) failed", step_id, kind)
            error = f"{type(e).__name__}: {e}"

        async with session_scope() as db:
            step = await db.get(Step, step_id)
            if step is None:
                return
            step.attempts = [
                *step.attempts,
                {
                    "attempt": attempt_no,
                    "started_at": attempt_started.isoformat(),
                    "finished_at": utcnow().isoformat(),
                    "error": error,
                },
            ]
            if error is not None:
                step.error = error
                if attempt_no < step.max_attempts:
                    delay = get_settings().queue.retry_delay_seconds
                    step.state = StepState.pending  # delayed auto-retry
                    step.scheduled_at = utcnow() + timedelta(seconds=delay)
                    await audit_record(
                        db, "task", "retry_scheduled",
                        step_id=step.id, step_kind=str(step.kind.value),
                        session_id=step.session_id, attempt=attempt_no,
                        scheduled_at=step.scheduled_at.isoformat(),
                        error=error[:300],
                    )
                else:
                    step.state = StepState.failed
                    step.finished_at = utcnow()
            else:
                step.error = None
                step.scheduled_at = None
                if verdict == AWAIT_USER:
                    step.state = StepState.awaiting_user
                else:
                    step.state = StepState.succeeded
                    step.finished_at = utcnow()
            session = await db.get(Session, step.session_id)
            if session is not None:
                await sync_session(db, session)
                if session.job_id is not None:
                    await update_job(db, session.job_id)
            await db.commit()
            _publish(step)


# Serializes job-counter updates (lost-update race between workers).
_job_update_lock = asyncio.Lock()


async def update_job(db: DbSession, job_id: int) -> None:
    """Job counters: a session counts as done when it reached a
    terminal, non-blocked position (done/failed)."""
    async with _job_update_lock:
        job = await db.get(Job, job_id)
        if job is None:
            return
        sessions = (
            await db.scalars(select(Session).where(Session.job_id == job_id))
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


async def recover() -> dict[str, int]:
    """Startup recovery: steps left running by a dead process are
    retried (attempt logged as interrupted) or failed when the budget is
    gone. Sessions re-derive their phase/status from their steps."""
    async with session_scope() as db:
        retried = failed = 0
        running = (
            await db.scalars(select(Step).where(Step.state == StepState.running))
        ).all()
        touched_sessions: set[int] = set()
        for step in running:
            step.attempts = [
                *step.attempts,
                {
                    "attempt": step.attempt_count,
                    "started_at": step.started_at.isoformat() if step.started_at else None,
                    "finished_at": None,
                    "error": "interrupted by app restart",
                },
            ]
            if step.attempt_count < step.max_attempts:
                step.state = StepState.pending
                step.scheduled_at = None
                retried += 1
            else:
                step.state = StepState.failed
                step.error = (step.error or "interrupted by app restart")
                step.finished_at = utcnow()
                failed += 1
            touched_sessions.add(step.session_id)
        for sid in touched_sessions:
            session = await db.get(Session, sid)
            if session is not None:
                await sync_session(db, session)
        await db.commit()
        return {"retried": retried, "failed": failed}


workers = StepWorkers()
