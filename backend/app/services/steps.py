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
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.orm import aliased, defer

from app.config import get_settings
from app.db.models import (
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
from app.paperless import PaperlessClient
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
        verify_tls=s.verify_tls,
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
            # A succeeded+resolved gate means analysis follows/finished
            # — unless the step is marked OCR-only, where the pipeline
            # deliberately ends at the gate.
            if p.state == StepState.succeeded and p.result.get("resolution"):
                phase = (
                    SessionPhase.done
                    if p.input.get("ocr_only")
                    else SessionPhase.analyzing
                )
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
    commit: bool = True,
) -> Step:
    """``commit=False`` lets a caller batch many steps into ONE
    transaction (bulk jobs): nothing is visible to workers until the
    caller commits and calls :func:`notify_steps` — no half-created
    jobs, no workers racing a loop that is still inserting."""
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
    if commit:
        await db.commit()
        notify_steps([step])
    return step


def notify_steps(steps: list[Step]) -> None:
    """Publish + wake AFTER the transaction that created the steps
    committed — events must never announce uncommitted state."""
    for step in steps:
        _publish(step)
    for lane in {s.lane for s in steps}:
        workers.wake(lane)


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
    # AUDIT SV-M6 (redo half): claim the redo atomically — two concurrent
    # redos of the same step must not both create successors.
    claimed = await db.execute(
        sa_update(Step)
        .where(Step.id == step.id, Step.state == step.state)
        .values(state=StepState.superseded)
    )
    if claimed.rowcount == 0:
        raise StepActionError("step was just redone or changed state; reload")
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
        s.state = StepState.superseded  # step itself already flipped above
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
    new = await create_step(
        db,
        session,
        step.kind,
        {**step.input, **(input_override or {})},
        lane=step.lane,
        supersedes_id=step.id,
    )
    # AUDIT SV-L1: supersessions are announced only after create_step's
    # commit made them real (events never announce uncommitted state).
    for s in to_supersede:
        _publish(s)
    return new


async def resolve_step(
    db: DbSession, paperless: PaperlessClient, step: Step, body: dict[str, Any]
) -> Step:
    """Resolve an awaiting_user step (kind-specific semantics).

    AUDIT SV-M6/SV-H3: the resolution is claimed atomically (two
    concurrent resolves → one wins, one 409s), and the follow-up step a
    resolver creates commits in the SAME transaction that marks this
    step succeeded — no window where the gate is still resolvable while
    its analysis is already queued."""
    if step.state != StepState.awaiting_user:
        raise StepActionError(f"step is {step.state.value}, not awaiting user input")
    _ensure_registered()
    resolver = RESOLVERS.get(step.kind)
    if resolver is None:
        raise StepActionError(f"steps of kind {step.kind.value} are not resolvable")
    claimed = await db.execute(
        sa_update(Step)
        .where(Step.id == step.id, Step.state == StepState.awaiting_user)
        .values(state=StepState.running)
    )
    if claimed.rowcount == 0:
        raise StepActionError("step is already being resolved")
    await db.commit()
    await db.refresh(step)
    session = await db.get(Session, step.session_id)
    assert session is not None
    try:
        created = await resolver(db, paperless, session, step, body)
        step.state = StepState.succeeded
        step.finished_at = utcnow()
        await sync_session(db, session)
        await db.commit()
    except Exception:
        await db.rollback()
        step.state = StepState.awaiting_user  # release the claim
        step.finished_at = None
        await db.commit()
        raise
    _publish(step)
    if created is not None:
        notify_steps([created])
    return step


# ----- executor/resolver registries -----------------------------------

# kind -> coroutine; filled by app.services.pipeline (the domain side).
# The engine never imports the pipeline at module load — registration is
# one-directional, the worker pulls it in lazily on first use.
EXECUTORS: dict[StepKind, Any] = {}
RESOLVERS: dict[StepKind, Any] = {}

_registered = False


def _ensure_registered() -> None:
    global _registered
    if not _registered:
        import app.services.pipeline  # noqa: F401  (registers executors)

        _registered = True


class StepWorkers:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._wakeups: dict[QueueLane, asyncio.Event] = {}
        # Per-lane: interactive claims must never queue behind batch
        # claims (AUDIT SV-L2). The SQL UPDATE is the real guard; these
        # locks only reduce claim contention within a lane.
        self._claim_locks: dict[QueueLane, asyncio.Lock] = {
            lane: asyncio.Lock() for lane in QueueLane
        }

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
        async with self._claim_locks[lane]:
            async with session_scope() as db:
                # AUDIT BC-F18: one turn per session at a time — a
                # session whose step is already running must not get a
                # second concurrent turn (message_history would be
                # last-writer-wins). Correlated NOT EXISTS on a running
                # sibling.
                sibling = aliased(Step)
                step_id = await db.scalar(
                    select(Step.id)
                    .where(
                        Step.state == StepState.pending,
                        Step.lane == lane,
                        (Step.scheduled_at.is_(None)) | (Step.scheduled_at <= utcnow()),
                        ~select(sibling.id)
                        .where(
                            sibling.session_id == Step.session_id,
                            sibling.state == StepState.running,
                        )
                        .exists(),
                    )
                    .order_by(Step.id)
                    .limit(1)
                )
                if step_id is None:
                    return None
                # Atomic claim: the UPDATE only wins if the step is STILL
                # pending — a second worker (or process) gets rowcount 0.
                # The asyncio lock above is an optimization, not the guard.
                claimed = await db.execute(
                    sa_update(Step)
                    .where(Step.id == step_id, Step.state == StepState.pending)
                    .values(
                        state=StepState.running,
                        attempt_count=Step.attempt_count + 1,
                    )
                )
                if claimed.rowcount == 0:
                    return None
                step = await db.get(Step, step_id)
                assert step is not None
                step.started_at = step.started_at or utcnow()
                # Engine paths never read the (potentially megabytes of)
                # serialized history — don't deserialize it per claim
                # (AUDIT SV-L4).
                session = await db.get(
                    Session, step.session_id,
                    options=[defer(Session.message_history)],
                )
                if session is not None:
                    await sync_session(db, session)
                await db.commit()
                _publish(step)
                return step.id

    async def _run(self, step_id: int) -> None:
        _ensure_registered()
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

        # The finalize transaction is pure bookkeeping and idempotent
        # per attempt — if it fails transiently (e.g. SQLite contention)
        # retry it instead of stranding the step in 'running' until the
        # next process restart (AUDIT SV-L8).
        for backoff in (0.5, 2.0, None):
            try:
                await self._finalize(step_id, attempt_no, attempt_started,
                                     error, verdict)
                return
            except Exception:  # noqa: BLE001
                if backoff is None:
                    log.exception(
                        "finalize for step %s failed repeatedly; step stays "
                        "running until recover()", step_id,
                    )
                    raise
                log.exception(
                    "finalize for step %s failed; retrying in %.1fs",
                    step_id, backoff,
                )
                await asyncio.sleep(backoff)

    async def _finalize(
        self,
        step_id: int,
        attempt_no: int,
        attempt_started,
        error: str | None,
        verdict: str | None,
    ) -> None:
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
            session = await db.get(
                Session, step.session_id,
                options=[defer(Session.message_history)],
            )
            if session is not None:
                await sync_session(db, session)
                if session.job_id is not None:
                    # Lazy: jobs.py imports create_step from here.
                    from app.services.jobs import update_job

                    await update_job(db, session.job_id)
            await db.commit()
            _publish(step)




async def cancel_job_steps(db: DbSession, job_id: int) -> list[Step]:
    """Cancel every still-pending step of a job's sessions. Running
    steps finish on their own; the sessions re-derive their status from
    the cancelled tail (single writer stays single).

    AUDIT SV-M2: the flip is guarded (`WHERE state='pending'`) so a
    worker that claims a step mid-cancel keeps it — we never overwrite
    'running'. Returns the cancelled steps; the CALLER commits and then
    publishes them (events never announce uncommitted state)."""
    ids = list(
        (
            await db.scalars(
                select(Step.id)
                .join(Session, Session.id == Step.session_id)
                .where(Session.job_id == job_id, Step.state == StepState.pending)
            )
        ).all()
    )
    if not ids:
        return []
    await db.execute(
        sa_update(Step)
        .where(Step.id.in_(ids), Step.state == StepState.pending)
        .values(
            state=StepState.cancelled,
            error="cancelled with its job",
            finished_at=utcnow(),
        )
    )
    cancelled = list(
        (
            await db.scalars(
                select(Step).where(
                    Step.id.in_(ids), Step.state == StepState.cancelled
                )
            )
        ).all()
    )
    for sid in {s.session_id for s in cancelled}:
        session = await db.get(
            Session, sid, options=[defer(Session.message_history)]
        )
        if session is not None:
            await sync_session(db, session)
    return cancelled


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
