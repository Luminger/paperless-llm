"""DB-backed queue: enqueue/claim/run, retries, recovery, job counters.

Uses a file-backed sqlite DB because the workers open their own
sessions via the app's global engine."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.config import reset_settings_cache
from app.db.models import (
    AgentKind,
    Job,
    JobStatus,
    QueueItem,
    QueueLane,
    QueueState,
    Session,
    SessionPhase,
    SessionStatus,
)
from app.db.session import dispose_engine, init_db, session_scope
from app.services import queue as queue_mod
from app.services.queue import QueueWorkers, enqueue, recover


@pytest.fixture
async def file_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PLLM_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/q.sqlite3")
    monkeypatch.setenv("PLLM_QUEUE__POLL_INTERVAL_SECONDS", "0.05")
    monkeypatch.setenv("PLLM_QUEUE__RETRY_ATTEMPTS", "2")  # 3 attempts total
    monkeypatch.setenv("PLLM_QUEUE__RETRY_DELAY_SECONDS", "0.1")
    reset_settings_cache()
    await dispose_engine()
    await init_db()
    yield
    await dispose_engine()
    reset_settings_cache()


async def _wait_for(predicate, limit=15.0):
    async def check():
        while True:
            if await predicate():
                return
            await asyncio.sleep(0.05)

    await asyncio.wait_for(check(), limit)


async def _item_state(item_id: int) -> QueueState:
    async with session_scope() as db:
        return (await db.get(QueueItem, item_id)).state


async def test_worker_runs_stage_to_done(file_db, monkeypatch):
    calls: list[dict] = []

    async def fake_stage(**kwargs):
        calls.append(kwargs)

    monkeypatch.setitem(queue_mod.STAGES, "start", fake_stage)
    async with session_scope() as db:
        item = await enqueue(db, "start", {"session_id": 1}, lane=QueueLane.interactive)
        item_id = item.id

    workers = QueueWorkers()
    await workers.start()
    try:
        await _wait_for(lambda: _is(item_id, QueueState.done))
    finally:
        await workers.stop()
    assert calls == [{"session_id": 1}]


def _is(item_id: int, state: QueueState):
    async def check() -> bool:
        return await _item_state(item_id) == state

    return check()


async def test_crashing_stage_retries_then_fails_session(file_db, monkeypatch):
    attempts: list[int] = []

    async def bad_stage(**kwargs):
        attempts.append(1)
        raise RuntimeError("boom")

    monkeypatch.setitem(queue_mod.STAGES, "start", bad_stage)
    async with session_scope() as db:
        s = Session(agent_kind=AgentKind.document, phase=SessionPhase.queued)
        db.add(s)
        await db.flush()
        session_id = s.id
        item = await enqueue(
            db, "start", {"session_id": s.id}, lane=QueueLane.batch, session_id=s.id
        )
        item_id = item.id

    workers = QueueWorkers()
    await workers.start()
    try:
        await _wait_for(_is_final(item_id))
    finally:
        await workers.stop()

    async with session_scope() as db:
        item = await db.get(QueueItem, item_id)
        assert item.state == QueueState.failed
        assert item.attempts == item.max_attempts
        assert "boom" in item.error
        session = await db.get(Session, session_id)
        assert session.status == SessionStatus.failed
    assert len(attempts) == 3


def _is_final(item_id: int):
    async def check() -> bool:
        state = await _item_state(item_id)
        return state in (QueueState.done, QueueState.failed)

    return check


async def test_stage_recorded_session_failure_retries_then_fails(file_db, monkeypatch):
    """Stages swallow their own errors (LLM/network hiccups) and mark
    the session failed; the retry policy re-runs them with the
    configured delay before the item finally fails."""

    async def stage_marks_failed(session_id: int):
        async with session_scope() as db:
            s = await db.get(Session, session_id)
            s.status = SessionStatus.failed
            s.error = "recorded by stage"
            await db.commit()

    monkeypatch.setitem(queue_mod.STAGES, "start", stage_marks_failed)
    async with session_scope() as db:
        s = Session(agent_kind=AgentKind.document, phase=SessionPhase.queued)
        db.add(s)
        await db.flush()
        item = await enqueue(
            db, "start", {"session_id": s.id}, lane=QueueLane.batch, session_id=s.id
        )
        item_id = item.id

    workers = QueueWorkers()
    await workers.start()
    try:
        await _wait_for(_is_final(item_id))
    finally:
        await workers.stop()

    async with session_scope() as db:
        item = await db.get(QueueItem, item_id)
        assert item.state == QueueState.failed
        assert item.attempts == item.max_attempts == 3  # retried per policy
        assert item.error == "recorded by stage"


async def test_job_counters_and_completion(file_db, monkeypatch):
    async def ok_stage(**kwargs):
        pass

    monkeypatch.setitem(queue_mod.STAGES, "start", ok_stage)
    async with session_scope() as db:
        job = Job(kind="bulk_analyze", total=2)
        db.add(job)
        await db.flush()
        job_id = job.id
        i1 = await enqueue(db, "start", {}, lane=QueueLane.batch, job_id=job.id)
        i2 = await enqueue(db, "start", {}, lane=QueueLane.batch, job_id=job.id)
        ids = (i1.id, i2.id)

    workers = QueueWorkers()
    await workers.start()
    try:
        for i in ids:
            await _wait_for(_is_final(i))
        await _wait_for(_job_status_is(job_id, JobStatus.completed))
    finally:
        await workers.stop()

    async with session_scope() as db:
        job = await db.get(Job, job_id)
        assert (job.done, job.failed) == (2, 0)


def _job_status_is(job_id: int, status: JobStatus):
    async def check() -> bool:
        async with session_scope() as db:
            return (await db.get(Job, job_id)).status == status

    return check


async def test_scheduled_items_wait_for_their_time(file_db):
    """Pending items with a future scheduled_at are not claimed."""
    from datetime import timedelta

    from app.db.models import utcnow

    async with session_scope() as db:
        db.add(
            QueueItem(
                stage="start", args={}, state=QueueState.pending,
                lane=QueueLane.batch, scheduled_at=utcnow() + timedelta(hours=1),
            )
        )
        await db.commit()

    workers = QueueWorkers()
    await workers.start()
    try:
        await asyncio.sleep(0.3)  # several poll cycles
        async with session_scope() as db:
            item = await db.scalar(select(QueueItem))
            assert item.state == QueueState.pending  # untouched
            assert item.attempts == 0
    finally:
        await workers.stop()


async def test_recover_retries_running_and_fails_orphans(file_db):
    async with session_scope() as db:
        # Crashed mid-run, attempts left -> retried.
        db.add(QueueItem(stage="start", args={}, state=QueueState.running, attempts=1))
        # Crashed with attempts exhausted -> failed (+ session failed).
        s1 = Session(agent_kind=AgentKind.document, phase=SessionPhase.analyzing)
        db.add(s1)
        await db.flush()
        db.add(
            QueueItem(
                stage="start", args={}, state=QueueState.running,
                attempts=3, session_id=s1.id,
            )
        )
        # In-flight session without any queue item -> orphan, failed.
        s2 = Session(agent_kind=AgentKind.document, phase=SessionPhase.ocr_running)
        db.add(s2)
        await db.commit()
        s1_id, s2_id = s1.id, s2.id

    stats = await recover()
    assert stats == {"retried": 1, "failed": 1, "orphaned": 1}

    async with session_scope() as db:
        states = sorted(
            i.state.value for i in (await db.scalars(select(QueueItem))).all()
        )
        assert states == ["failed", "pending"]
        assert (await db.get(Session, s1_id)).status == SessionStatus.failed
        assert (await db.get(Session, s2_id)).status == SessionStatus.failed


async def test_attempt_log_records_every_attempt(file_db, monkeypatch):
    """Retries never shadow earlier attempts: each one is appended to
    the item's log with its error."""

    async def bad_stage(**kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setitem(queue_mod.STAGES, "start", bad_stage)
    async with session_scope() as db:
        item = await enqueue(db, "start", {}, lane=QueueLane.batch)
        item_id = item.id

    workers = QueueWorkers()
    await workers.start()
    try:
        await _wait_for(_is_final(item_id))
    finally:
        await workers.stop()

    async with session_scope() as db:
        item = await db.get(QueueItem, item_id)
        assert [a["attempt"] for a in item.attempt_log] == [1, 2, 3]
        assert all("kaputt" in a["error"] for a in item.attempt_log)
        assert all(a["started_at"] and a["finished_at"] for a in item.attempt_log)
