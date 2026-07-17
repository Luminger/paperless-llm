"""Step engine: the generic session-work unit. Creation, execution,
awaiting_user, retry policy, generic actions, recovery, job counting.

File-backed sqlite because workers open their own sessions via the
app's global engine."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

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
        session = await db.get(Session, sid)
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
        session = await db.get(Session, sid)
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
    assert stats == {"retried": 1, "failed": 1}

    async with session_scope() as db:
        states = sorted(s.state.value for s in (await db.scalars(select(Step))).all())
        assert states == ["failed", "pending"]
        for s in (await db.scalars(select(Step))).all():
            assert s.attempts[-1]["error"] == "interrupted by app restart"


async def test_job_counters_from_sessions(file_db, monkeypatch):
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
        await _wait_for(_job_is(job_id, JobStatus.completed))
    finally:
        await workers.stop()

    async with session_scope() as db:
        job = await db.get(Job, job_id)
        assert (job.done, job.failed) == (2, 0)


def _job_is(job_id: int, status: JobStatus):
    async def check() -> bool:
        async with session_scope() as db:
            return (await db.get(Job, job_id)).status == status

    return check
