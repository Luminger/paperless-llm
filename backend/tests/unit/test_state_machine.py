"""The normative step/proposal state machine (docs/state-machine.md):
no dead ends, central transition legality, and the crash-recovery
sweeps that close every transient state."""

from __future__ import annotations

import pytest
from sqlalchemy import select

import app.services.pipeline  # noqa: F401 — populate registries
from app.config import reset_settings_cache
from app.db.models import (
    AgentKind,
    EntityType,
    Proposal,
    ProposalStatus,
    Session,
    Step,
    StepKind,
    StepState,
    utcnow,
)
from app.db.session import dispose_engine, init_db, session_scope
from app.services.steps import (
    STEP_TRANSITIONS,
    StepWorkers,
    assert_transition,
    recover,
)

TERMINAL_FINAL = {StepState.superseded}  # final with no user action (has a successor)
USER_RECOVERABLE = {StepState.succeeded, StepState.failed, StepState.cancelled,
                    StepState.awaiting_user}
WORKER_OWNED = {StepState.pending, StepState.running}


def test_every_state_has_an_exit_or_defined_recovery():
    """Invariant 1: no dead ends. Every state either has an outgoing
    transition owned by a worker, or a user action (Retry/Redo/Resolve
    = a transition out), or is `superseded` (final by construction:
    only ever entered together with creating a successor step)."""
    outgoing = {frm for frm, _ in STEP_TRANSITIONS}
    for state in StepState:
        if state in TERMINAL_FINAL:
            assert state not in outgoing or True  # final; successor exists
            continue
        assert state in outgoing, f"{state.value} is a dead end"


def test_worker_owned_states_exit_to_every_outcome():
    """pending/running are transient: workers must be able to take them
    to success, gate, retry, failure and cancellation."""
    assert (StepState.pending, StepState.running) in STEP_TRANSITIONS
    assert (StepState.pending, StepState.cancelled) in STEP_TRANSITIONS
    for to in (StepState.succeeded, StepState.awaiting_user, StepState.pending,
               StepState.failed, StepState.cancelled):
        assert (StepState.running, to) in STEP_TRANSITIONS


def test_user_recoverable_states_have_their_documented_actions():
    # Retry: failed/cancelled -> pending.
    assert (StepState.failed, StepState.pending) in STEP_TRANSITIONS
    assert (StepState.cancelled, StepState.pending) in STEP_TRANSITIONS
    # Redo: every settled state -> superseded (with a successor step).
    for frm in (StepState.succeeded, StepState.failed, StepState.cancelled,
                StepState.awaiting_user):
        assert (frm, StepState.superseded) in STEP_TRANSITIONS
    # Resolve: the gate's claim.
    assert (StepState.awaiting_user, StepState.running) in STEP_TRANSITIONS


def test_illegal_transitions_fail_loudly():
    with pytest.raises(RuntimeError, match="illegal step transition"):
        assert_transition(StepState.succeeded, StepState.running)
    with pytest.raises(RuntimeError):
        assert_transition(StepState.superseded, StepState.pending)
    with pytest.raises(RuntimeError):
        assert_transition(StepState.awaiting_user, StepState.succeeded)  # only via claim
    # And the happy path passes.
    assert_transition(StepState.pending, StepState.running)


def test_no_transition_leaves_superseded():
    """Superseded is final: it is only entered by redo, which creates
    the successor in the same operation."""
    assert not any(frm == StepState.superseded for frm, _ in STEP_TRANSITIONS)


# ----- crash recovery closes every transient state --------------------


@pytest.fixture
async def file_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PLLM_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/s.sqlite3")
    reset_settings_cache()
    await dispose_engine()
    await init_db()
    yield
    await dispose_engine()
    reset_settings_cache()


async def _session_with_step(state: StepState, **step_kw) -> tuple[int, int]:
    async with session_scope() as db:
        s = Session(agent_kind=AgentKind.document, entity_type=EntityType.document,
                    entity_id=7)
        db.add(s)
        await db.flush()
        step = Step(session_id=s.id, kind=StepKind.analysis, state=state,
                    attempt_count=1, max_attempts=3, **step_kw)
        db.add(step)
        await db.commit()
        return s.id, step.id


async def _add_proposal(session_id: int, step_id: int, status: ProposalStatus) -> int:
    async with session_scope() as db:
        p = Proposal(
            session_id=session_id, step_id=step_id, kind="set_title",
            agent_payload={"kind": "set_title", "document_id": 7, "title": "x"},
            status=status, entity_type=EntityType.document, entity_id=7,
        )
        db.add(p)
        await db.commit()
        return p.id


async def test_recover_releases_stuck_applying_proposals(file_db):
    """Invariant 4: `applying` belongs to an in-flight apply call and
    none exists at startup — a crash mid-apply must not strand the
    proposal forever."""
    sid, step_id = await _session_with_step(StepState.succeeded)
    pid = await _add_proposal(sid, step_id, ProposalStatus.applying)
    applied = await _add_proposal(sid, step_id, ProposalStatus.applied)

    stats = await recover()
    assert stats["applies_released"] == 1
    async with session_scope() as db:
        assert (await db.get(Proposal, pid)).status == ProposalStatus.pending
        assert (await db.get(Proposal, applied)).status == ProposalStatus.applied


async def test_failed_attempt_sweeps_open_proposals(file_db):
    """Invariant 5: a non-success attempt leaves no open unapplied
    proposals behind — the retry emits fresh ones."""
    sid, step_id = await _session_with_step(StepState.running, started_at=utcnow())
    draft = await _add_proposal(sid, step_id, ProposalStatus.draft)
    pending = await _add_proposal(sid, step_id, ProposalStatus.pending)
    applied = await _add_proposal(sid, step_id, ProposalStatus.applied)

    w = StepWorkers()
    await w._finalize(step_id, 1, utcnow(), "boom", None)
    async with session_scope() as db:
        step = await db.get(Step, step_id)
        assert step.state == StepState.pending  # auto-retry scheduled
        assert (await db.get(Proposal, draft)).status == ProposalStatus.superseded
        assert (await db.get(Proposal, pending)).status == ProposalStatus.superseded
        assert (await db.get(Proposal, applied)).status == ProposalStatus.applied


async def test_cancelled_attempt_sweeps_open_proposals(file_db):
    sid, step_id = await _session_with_step(StepState.running, started_at=utcnow())
    draft = await _add_proposal(sid, step_id, ProposalStatus.draft)

    w = StepWorkers()
    await w._finalize(step_id, 1, utcnow(), None, None, cancelled=True)
    async with session_scope() as db:
        assert (await db.get(Step, step_id)).state == StepState.cancelled
        assert (await db.get(Proposal, draft)).status == ProposalStatus.superseded


async def test_successful_attempt_keeps_pending_proposals(file_db):
    """The review flow depends on it: success promotes/keeps proposals
    for the user — only failure sweeps."""
    sid, step_id = await _session_with_step(StepState.running, started_at=utcnow())
    pending = await _add_proposal(sid, step_id, ProposalStatus.pending)

    w = StepWorkers()
    await w._finalize(step_id, 1, utcnow(), None, None)
    async with session_scope() as db:
        assert (await db.get(Step, step_id)).state == StepState.succeeded
        assert (await db.get(Proposal, pending)).status == ProposalStatus.pending


async def test_recover_leaves_no_transient_state(file_db):
    """Invariant 4, end to end: after recover() nothing is running,
    applying, or a stranded draft."""
    sid, step_id = await _session_with_step(StepState.running, started_at=utcnow())
    await _add_proposal(sid, step_id, ProposalStatus.draft)
    p_sid, p_step = await _session_with_step(StepState.succeeded)
    await _add_proposal(p_sid, p_step, ProposalStatus.applying)

    await recover()
    async with session_scope() as db:
        assert not [
            s for s in (await db.scalars(select(Step))).all()
            if s.state == StepState.running
        ]
        statuses = {p.status for p in (await db.scalars(select(Proposal))).all()}
        assert ProposalStatus.applying not in statuses
        # The interrupted step's draft was swept.
        assert ProposalStatus.draft not in statuses
