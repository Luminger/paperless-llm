"""Step engine: the generic session-work unit. Creation, execution,
awaiting_user, retry policy, generic actions, recovery, job counting.

File-backed sqlite because workers open their own sessions via the
app's global engine."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

import app.services.pipeline  # noqa: F401 — populate EXECUTORS/RESOLVERS at

# collection time so per-test monkeypatches of them stick regardless of
# which test module imported the pipeline first (order-dependence fix).
from app.config import reset_settings_cache
from app.db.models import (
    AgentKind,
    EntityType,
    Job,
    JobStatus,
    Session,
    SessionPhase,
    SessionStatus,
    Step,
    StepKind,
    StepState,
)
from app.db.session import dispose_engine, init_db, session_scope
from app.services import steps as engine
from app.services.steps import (
    StepWorkers,
    create_step,
    recover,
    redo_step,
    retry_step,
)


@pytest.fixture
async def file_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PLLM_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/s.sqlite3")
    monkeypatch.setenv("PLLM_QUEUE__POLL_INTERVAL_SECONDS", "0.05")
    monkeypatch.setenv("PLLM_QUEUE__RETRY_ATTEMPTS", "2")  # 3 attempts total
    monkeypatch.setenv("PLLM_QUEUE__RETRY_DELAY_SECONDS", "0.1")
    reset_settings_cache()
    await dispose_engine()
    await init_db()
    yield
    await dispose_engine()
    reset_settings_cache()


async def _make_session(**kw) -> int:
    async with session_scope() as db:
        s = Session(agent_kind=AgentKind.document, entity_type=EntityType.document,
                    entity_id=7, **kw)
        db.add(s)
        await db.commit()
        return s.id


async def _wait_for(predicate, limit=15.0):
    async def check():
        while True:
            if await predicate():
                return
            await asyncio.sleep(0.05)

    await asyncio.wait_for(check(), limit)


def _step_in(step_id: int, *states: StepState):
    async def check() -> bool:
        async with session_scope() as db:
            return (await db.get(Step, step_id)).state in states

    return check


FINAL = (StepState.succeeded, StepState.failed, StepState.awaiting_user)


async def test_step_runs_to_success_and_syncs_session(file_db, monkeypatch):
    async def ok(db, paperless, session, step):
        step.result = {"hello": 1}
        return None

    monkeypatch.setitem(engine.EXECUTORS, StepKind.analysis, ok)
    sid = await _make_session()
    async with session_scope() as db:
        session = await db.get(Session, sid)
        step = await create_step(db, session, StepKind.analysis)
        step_id = step.id
        assert session.phase == SessionPhase.queued  # derived immediately

    workers = StepWorkers()
    await workers.start()
    try:
        await _wait_for(_step_in(step_id, *FINAL))
    finally:
        await workers.stop()

    async with session_scope() as db:
        step = await db.get(Step, step_id)
        assert step.state == StepState.succeeded
        assert step.result == {"hello": 1}
        assert len(step.attempts) == 1 and step.attempts[0]["error"] is None
        session = await db.get(Session, sid)
        assert session.phase == SessionPhase.done
        assert session.status == SessionStatus.idle


async def test_awaiting_user_pauses_and_resolution_continues(file_db, monkeypatch):
    async def gate(db, paperless, session, step):
        step.result = {"pages": 1}
        return engine.AWAIT_USER

    monkeypatch.setitem(engine.EXECUTORS, StepKind.ocr, gate)
    sid = await _make_session(params={"redo_ocr": True})
    async with session_scope() as db:
        session = await db.get(Session, sid)
        step = await create_step(db, session, StepKind.ocr)
        step_id = step.id

    workers = StepWorkers()
    await workers.start()
    try:
        await _wait_for(_step_in(step_id, *FINAL))
    finally:
        await workers.stop()

    async with session_scope() as db:
        step = await db.get(Step, step_id)
        assert step.state == StepState.awaiting_user
        session = await db.get(Session, sid)
        assert session.phase == SessionPhase.ocr_review  # the gate
        assert session.status == SessionStatus.idle


async def test_failure_retries_with_delay_then_fails(file_db, monkeypatch):
    calls: list[int] = []

    async def boom(db, paperless, session, step):
        calls.append(1)
        raise RuntimeError("kaputt")

    monkeypatch.setitem(engine.EXECUTORS, StepKind.analysis, boom)
    sid = await _make_session()
    async with session_scope() as db:
        session = await db.get(Session, sid)
        step = await create_step(db, session, StepKind.analysis)
        step_id = step.id

    workers = StepWorkers()
    await workers.start()
    try:
        await _wait_for(_step_in(step_id, StepState.failed))
    finally:
        await workers.stop()

    async with session_scope() as db:
        step = await db.get(Step, step_id)
        # Every attempt in the log, never shadowed.
        assert [a["attempt"] for a in step.attempts] == [1, 2, 3]
        assert all("kaputt" in a["error"] for a in step.attempts)
        session = await db.get(Session, sid)
        assert session.status == SessionStatus.failed
        assert "kaputt" in session.error
    assert len(calls) == 3


async def test_manual_retry_revives_failed_step_with_fresh_budget(file_db, monkeypatch):
    async def ok(db, paperless, session, step):
        return None

    monkeypatch.setitem(engine.EXECUTORS, StepKind.analysis, ok)
    sid = await _make_session()
    async with session_scope() as db:
        step = Step(session_id=sid, kind=StepKind.analysis, state=StepState.failed,
                    attempt_count=3, max_attempts=3, error="old error",
                    attempts=[{"attempt": 1}, {"attempt": 2}, {"attempt": 3}])
        db.add(step)
        await db.commit()
        step_id = step.id

    async with session_scope() as db:
        step = await db.get(Step, step_id)
        await retry_step(db, step)
        assert step.state == StepState.pending
        assert step.attempt_count == 0
        # History preserved + manual marker appended.
        assert step.attempts[-1].get("manual_retry_at")

    workers = StepWorkers()
    await workers.start()
    try:
        await _wait_for(_step_in(step_id, StepState.succeeded))
    finally:
        await workers.stop()


async def test_redo_supersedes_and_merges_input(file_db, monkeypatch):
    sid = await _make_session()
    async with session_scope() as db:
        step = Step(session_id=sid, kind=StepKind.ocr, state=StepState.awaiting_user,
                    input={"dpi": 150})
        db.add(step)
        await db.commit()
        old_id = step.id

    async with session_scope() as db:
        step = await db.get(Step, old_id)
        new = await redo_step(db, step, {"instructions": "mind the stamp"})
        assert step.state == StepState.superseded
        assert new.kind == StepKind.ocr
        assert new.input == {"dpi": 150, "instructions": "mind the stamp"}
        assert new.supersedes_id == old_id
        assert new.state == StepState.pending

    # Running steps cannot be redone.
    async with session_scope() as db:
        running = Step(session_id=sid, kind=StepKind.ocr, state=StepState.running)
        db.add(running)
        await db.commit()
        with pytest.raises(engine.StepActionError):
            await redo_step(db, running)


async def test_recover_retries_interrupted_running_steps(file_db):
    sid = await _make_session(status=SessionStatus.running, phase=SessionPhase.analyzing)
    async with session_scope() as db:
        db.add(Step(session_id=sid, kind=StepKind.analysis, state=StepState.running,
                    attempt_count=1, max_attempts=3))
        db.add(Step(session_id=sid, kind=StepKind.chat, state=StepState.running,
                    attempt_count=3, max_attempts=3, error="e"))
        await db.commit()

    stats = await recover()
    assert stats == {"retried": 1, "failed": 1, "drafts_swept": 0}

    async with session_scope() as db:
        states = sorted(s.state.value for s in (await db.scalars(select(Step))).all())
        assert states == ["failed", "pending"]
        for s in (await db.scalars(select(Step))).all():
            assert s.attempts[-1]["error"] == "interrupted by app restart"


async def test_job_state_derived_from_sessions(file_db, monkeypatch):
    """AUDIT SV-M1: job state is COMPUTED from the sessions at read time
    (live_job_counts/apply_live) — there is no stored-counter
    maintenance."""
    async def ok(db, paperless, session, step):
        return None

    monkeypatch.setitem(engine.EXECUTORS, StepKind.analysis, ok)
    async with session_scope() as db:
        job = Job(kind="bulk_analyze", total=2)
        db.add(job)
        await db.flush()
        job_id = job.id
        step_ids = []
        for doc in (11, 13):
            s = Session(agent_kind=AgentKind.document, entity_type=EntityType.document,
                        entity_id=doc, job_id=job_id)
            db.add(s)
            await db.flush()
            step = await create_step(db, s, StepKind.analysis)
            step_ids.append(step.id)

    workers = StepWorkers()
    await workers.start()
    try:
        for i in step_ids:
            await _wait_for(_step_in(i, *FINAL))
    finally:
        await workers.stop()

    from app.api.schemas import JobOut
    from app.services.jobs import apply_live, live_job_counts

    async with session_scope() as db:
        job = await db.get(Job, job_id)
        out = apply_live(
            JobOut.model_validate(job),
            (await live_job_counts(db, [job_id])).get(job_id, (0, 0, 0)),
        )
        assert out.status == JobStatus.completed
        assert (out.done, out.failed) == (2, 0)


async def test_redo_supersedes_downstream_steps_and_open_proposals(file_db):
    """Redoing a step invalidates everything built on top of it: later
    steps AND their open proposals are superseded; applied ones are
    history and stay."""
    from app.db.models import Proposal, ProposalStatus

    sid = await _make_session()
    async with session_scope() as db:
        ocr = Step(session_id=sid, kind=StepKind.ocr, state=StepState.succeeded,
                   result={"resolution": "accepted"})
        db.add(ocr)
        await db.flush()
        analysis = Step(session_id=sid, kind=StepKind.analysis, state=StepState.succeeded)
        db.add(analysis)
        await db.flush()
        chat = Step(session_id=sid, kind=StepKind.chat, state=StepState.succeeded)
        db.add(chat)
        await db.flush()
        p_open = Proposal(session_id=sid, step_id=analysis.id, kind="update_document_metadata",
                          agent_payload={}, status=ProposalStatus.pending)
        p_applied = Proposal(session_id=sid, step_id=analysis.id, kind="create_entity",
                             agent_payload={}, status=ProposalStatus.applied)
        db.add_all([p_open, p_applied])
        await db.commit()
        ocr_id, analysis_id, chat_id = ocr.id, analysis.id, chat.id
        p_open_id, p_applied_id = p_open.id, p_applied.id

    async with session_scope() as db:
        step = await db.get(Step, ocr_id)
        new = await redo_step(db, step, {"instructions": "again, better"})
        assert new.supersedes_id == ocr_id

    async with session_scope() as db:
        assert (await db.get(Step, ocr_id)).state == StepState.superseded
        assert (await db.get(Step, analysis_id)).state == StepState.superseded
        assert (await db.get(Step, chat_id)).state == StepState.superseded
        assert (await db.get(Proposal, p_open_id)).status == ProposalStatus.superseded
        assert (await db.get(Proposal, p_applied_id)).status == ProposalStatus.applied


async def test_redo_refused_while_downstream_work_in_flight(file_db):
    sid = await _make_session()
    async with session_scope() as db:
        ocr = Step(session_id=sid, kind=StepKind.ocr, state=StepState.succeeded)
        db.add(ocr)
        await db.flush()
        db.add(Step(session_id=sid, kind=StepKind.analysis, state=StepState.running))
        await db.commit()
        ocr_id = ocr.id

    async with session_scope() as db:
        step = await db.get(Step, ocr_id)
        with pytest.raises(engine.StepActionError, match="still queued or running"):
            await redo_step(db, step)


# ----- AUDIT SV-M2 / SV-M6 / BC-F18 regression tests -------------------


async def test_cancel_never_overwrites_claimed_steps(file_db):
    """AUDIT SV-M2: the pending→cancelled flip is guarded — a step a
    worker claimed (running) mid-cancel keeps running."""
    from app.db.models import Job
    from app.services.steps import cancel_job_steps

    async with session_scope() as db:
        job = Job(kind="bulk_analysis", params={}, total=1)
        db.add(job)
        await db.flush()
        s = Session(agent_kind=AgentKind.document, entity_type=EntityType.document,
                    entity_id=7, job_id=job.id)
        db.add(s)
        await db.flush()
        pending = Step(session_id=s.id, kind=StepKind.analysis,
                       state=StepState.pending)
        running = Step(session_id=s.id, kind=StepKind.analysis,
                       state=StepState.running)
        db.add_all([pending, running])
        await db.commit()
        job_id, pending_id, running_id = job.id, pending.id, running.id

    async with session_scope() as db:
        cancelled = await cancel_job_steps(db, job_id)
        await db.commit()
        assert [c.id for c in cancelled] == [pending_id]

    async with session_scope() as db:
        assert (await db.get(Step, pending_id)).state == StepState.cancelled
        assert (await db.get(Step, running_id)).state == StepState.running


async def test_resolve_step_claims_atomically(file_db, monkeypatch):
    """AUDIT SV-M6: a step resolved out from under us → 409, resolver
    never runs twice."""
    from app.services import steps as engine_mod
    from app.services.steps import StepActionError, resolve_step

    calls = {"n": 0}

    async def fake_resolver(db, paperless, session, step, body):
        calls["n"] += 1
        return None

    monkeypatch.setitem(engine_mod.RESOLVERS, StepKind.chat, fake_resolver)
    monkeypatch.setattr(engine_mod, "_registered", True)

    sid = await _make_session()
    async with session_scope() as db:
        step = Step(session_id=sid, kind=StepKind.chat,
                    state=StepState.awaiting_user)
        db.add(step)
        await db.commit()
        step_id = step.id

    async with session_scope() as db:
        step = await db.get(Step, step_id)
        # A concurrent resolve wins the claim between our load and ours.
        from sqlalchemy import update as sa_update
        async with session_scope() as other:
            await other.execute(
                sa_update(Step).where(Step.id == step_id)
                .values(state=StepState.succeeded)
            )
            await other.commit()
        import pytest
        with pytest.raises(StepActionError):
            await resolve_step(db, None, step, {})
    assert calls["n"] == 0


async def test_resolve_step_commits_followup_with_the_gate(file_db, monkeypatch):
    """AUDIT SV-H3: the resolver's follow-up step becomes visible in the
    same transaction that marks the gate succeeded."""
    from app.services import steps as engine_mod
    from app.services.steps import resolve_step

    async def fake_resolver(db, paperless, session, step, body):
        return await create_step(
            db, session, StepKind.analysis, {"gate": "ok"}, commit=False
        )

    monkeypatch.setitem(engine_mod.RESOLVERS, StepKind.chat, fake_resolver)
    monkeypatch.setattr(engine_mod, "_registered", True)

    sid = await _make_session()
    async with session_scope() as db:
        step = Step(session_id=sid, kind=StepKind.chat,
                    state=StepState.awaiting_user)
        db.add(step)
        await db.commit()
        step_id = step.id

    async with session_scope() as db:
        step = await db.get(Step, step_id)
        await resolve_step(db, None, step, {})

    async with session_scope() as db:
        steps = (
            await db.scalars(select(Step).where(Step.session_id == sid).order_by(Step.id))
        ).all()
        assert [s.state for s in steps] == [StepState.succeeded, StepState.pending]
        assert steps[1].kind == StepKind.analysis


async def test_claim_skips_sessions_with_a_running_step(file_db):
    """AUDIT BC-F18: one turn per session — a pending step whose session
    already has a running step is not claimable; other sessions are."""
    workers = StepWorkers()

    busy_sid = await _make_session()
    free_sid = await _make_session()
    async with session_scope() as db:
        db.add(Step(session_id=busy_sid, kind=StepKind.chat,
                    state=StepState.running))
        blocked = Step(session_id=busy_sid, kind=StepKind.chat,
                       state=StepState.pending)
        free = Step(session_id=free_sid, kind=StepKind.chat,
                    state=StepState.pending)
        db.add_all([blocked, free])
        await db.commit()
        blocked_id, free_id = blocked.id, free.id

    from app.db.models import QueueLane

    first = await workers._claim(QueueLane.interactive)
    assert first == free_id  # skipped the blocked session's step
    second = await workers._claim(QueueLane.interactive)
    assert second is None  # blocked stays unclaimable, nothing else left

    async with session_scope() as db:
        assert (await db.get(Step, blocked_id)).state == StepState.pending


async def test_cancel_aborts_running_llm_execution(file_db, monkeypatch):
    """A user cancel kills the in-flight execution (the stuck-LLM-call
    escape hatch), finalizes the step as cancelled — and the worker
    survives to run the next step."""
    started = asyncio.Event()

    async def stuck(db, paperless, session, step):
        started.set()
        await asyncio.sleep(60)  # "never" returns

    async def ok(db, paperless, session, step):
        return None

    monkeypatch.setitem(engine.EXECUTORS, StepKind.analysis, stuck)
    sid = await _make_session()
    async with session_scope() as db:
        session = await db.get(Session, sid)
        step = await create_step(db, session, StepKind.analysis)
        step_id = step.id

    # The service targets the process-global workers.
    engine.workers._cancel_requested.clear()
    await engine.workers.start()
    try:
        await asyncio.wait_for(started.wait(), 15)
        async with session_scope() as db:
            from app.services.steps import cancel_session_steps

            flipped = await cancel_session_steps(db, sid)
            await db.commit()
            assert flipped == []  # running step is aborted, not DB-flipped
        await _wait_for(_step_in(step_id, StepState.cancelled))

        # Worker survived: a fresh step still gets executed.
        monkeypatch.setitem(engine.EXECUTORS, StepKind.analysis, ok)
        async with session_scope() as db:
            session = await db.get(Session, sid)
            step2 = await create_step(db, session, StepKind.analysis)
            step2_id = step2.id
        await _wait_for(_step_in(step2_id, StepState.succeeded))
    finally:
        await engine.workers.stop()

    async with session_scope() as db:
        step = await db.get(Step, step_id)
        assert step.state == StepState.cancelled
        assert step.error == "stopped by user"
        assert step.finished_at is not None
        assert step.attempts[-1]["error"] == "stopped by user"


async def test_cancel_flips_pending_steps_and_syncs_session(file_db):
    """No worker involved: pending steps flip directly (guarded), the
    session re-derives."""
    from app.services.steps import cancel_session_steps

    sid = await _make_session()
    async with session_scope() as db:
        session = await db.get(Session, sid)
        step = await create_step(db, session, StepKind.analysis)
        step_id = step.id

    async with session_scope() as db:
        flipped = await cancel_session_steps(db, sid)
        await db.commit()
        assert [s.id for s in flipped] == [step_id]

    async with session_scope() as db:
        step = await db.get(Step, step_id)
        assert step.state == StepState.cancelled
        assert step.error == "stopped by user"
        session = await db.get(Session, sid)
        assert session.status == SessionStatus.idle  # not failed


async def test_cancelled_step_revives_via_retry(file_db, monkeypatch):
    """Cancel is fully recoverable: Retry gives the step a fresh run."""

    async def ok(db, paperless, session, step):
        return None

    monkeypatch.setitem(engine.EXECUTORS, StepKind.analysis, ok)
    from app.services.steps import cancel_session_steps

    sid = await _make_session()
    async with session_scope() as db:
        session = await db.get(Session, sid)
        step = await create_step(db, session, StepKind.analysis)
        step_id = step.id
    async with session_scope() as db:
        await cancel_session_steps(db, sid)
        await db.commit()

    async with session_scope() as db:
        step = await db.get(Step, step_id)
        await retry_step(db, step)
        assert step.state == StepState.pending

    workers = StepWorkers()
    await workers.start()
    try:
        await _wait_for(_step_in(step_id, *FINAL))
    finally:
        await workers.stop()
    async with session_scope() as db:
        assert (await db.get(Step, step_id)).state == StepState.succeeded


async def test_paused_job_steps_are_not_claimed_until_resume(file_db, monkeypatch):
    """Pause is a job-row flip: pending steps stay pending but workers
    skip them; resume makes them claimable again."""
    from app.db.models import Job, JobStatus

    ran = asyncio.Event()

    async def ok(db, paperless, session, step):
        ran.set()
        return None

    monkeypatch.setitem(engine.EXECUTORS, StepKind.analysis, ok)
    async with session_scope() as db:
        job = Job(kind="bulk_analyze", params={}, total=1, status=JobStatus.paused)
        db.add(job)
        await db.flush()
        s = Session(agent_kind=AgentKind.document, entity_type=EntityType.document,
                    entity_id=7, job_id=job.id)
        db.add(s)
        await db.flush()
        step = Step(session_id=s.id, kind=StepKind.analysis, state=StepState.pending)
        db.add(step)
        await db.commit()
        job_id, step_id = job.id, step.id

    workers = StepWorkers()
    await workers.start()
    try:
        # Grace window: the step must NOT be picked up while paused.
        await asyncio.sleep(0.4)
        assert not ran.is_set()
        async with session_scope() as db:
            assert (await db.get(Step, step_id)).state == StepState.pending

        # Resume -> claimable.
        async with session_scope() as db:
            (await db.get(Job, job_id)).status = JobStatus.queued
            await db.commit()
        await _wait_for(_step_in(step_id, *FINAL))
    finally:
        await workers.stop()
    assert ran.is_set()


async def test_sessions_without_jobs_claim_normally_alongside_paused(file_db, monkeypatch):
    """The pause exclusion must not leak onto job-less sessions."""
    from app.db.models import Job, JobStatus

    async def ok(db, paperless, session, step):
        return None

    monkeypatch.setitem(engine.EXECUTORS, StepKind.analysis, ok)
    async with session_scope() as db:
        job = Job(kind="bulk_analyze", params={}, total=1, status=JobStatus.paused)
        db.add(job)
        await db.flush()
        jobbed = Session(agent_kind=AgentKind.document, entity_type=EntityType.document,
                         entity_id=7, job_id=job.id)
        free = Session(agent_kind=AgentKind.document, entity_type=EntityType.document,
                       entity_id=8)
        db.add_all([jobbed, free])
        await db.flush()
        blocked = Step(session_id=jobbed.id, kind=StepKind.analysis,
                       state=StepState.pending)
        runnable = Step(session_id=free.id, kind=StepKind.analysis,
                        state=StepState.pending)
        db.add_all([blocked, runnable])
        await db.commit()
        blocked_id, runnable_id = blocked.id, runnable.id

    workers = StepWorkers()
    await workers.start()
    try:
        await _wait_for(_step_in(runnable_id, *FINAL))
    finally:
        await workers.stop()
    async with session_scope() as db:
        assert (await db.get(Step, runnable_id)).state == StepState.succeeded
        assert (await db.get(Step, blocked_id)).state == StepState.pending


async def test_cancelled_session_phase_is_stopped(file_db):
    """A stopped run must not read as 'OCR running'/'Analyzing' in
    lists — its phase is 'stopped'."""
    from app.db.models import SessionPhase as SP
    from app.services.steps import cancel_session_steps

    sid = await _make_session()
    async with session_scope() as db:
        session = await db.get(Session, sid)
        await create_step(db, session, StepKind.ocr)
    async with session_scope() as db:
        await cancel_session_steps(db, sid)
        await db.commit()
    async with session_scope() as db:
        session = await db.get(Session, sid)
        assert session.phase == SP.stopped
        assert session.status == SessionStatus.idle
