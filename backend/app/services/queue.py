"""Persistent work queue: DB rows + in-process async workers.

Deliberately NOT celery/redis: this is a single-node tool whose real
concurrency cap is the LLM endpoint (max_concurrent per profile), the
SSE event bus is in-process, and a DB-backed queue gives strictly better
restart behavior (queued work survives; running work is retried). The
stage functions stay queue-agnostic, so a distributed queue remains a
contained swap if multi-node ever becomes real.

Two lanes so chat turns never wait behind bulk campaigns.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import (
    Job,
    JobStatus,
    QueueItem,
    QueueLane,
    QueueState,
    Session,
    SessionPhase,
    SessionStatus,
    utcnow,
)
from app.db.session import session_scope
from app.services import pipeline
from app.services.events import bus

log = logging.getLogger(__name__)

# Stage dispatch: queue rows name a stage; args are keyword arguments.
STAGES = {
    "start": pipeline.run_stage_start,
    "analysis": pipeline.run_stage_analysis,
    "steering": pipeline.run_stage_steering,
    "reocr": pipeline.run_stage_reocr,
}


async def enqueue(
    db: AsyncSession,
    stage: str,
    args: dict[str, Any] | None = None,
    *,
    lane: QueueLane = QueueLane.batch,
    session_id: int | None = None,
    job_id: int | None = None,
    commit: bool = True,
) -> QueueItem:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    item = QueueItem(
        lane=lane,
        stage=stage,
        args=args or {},
        session_id=session_id,
        job_id=job_id,
        max_attempts=1 + max(0, get_settings().queue.retry_attempts),
    )
    db.add(item)
    if commit:
        await db.commit()
    else:
        await db.flush()
    workers.wake(lane)
    return item


class QueueWorkers:
    """Async worker pool; one shared claim lock (single process)."""

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
        log.info("queue workers started (%d tasks)", len(self._tasks))

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
                item_id = await self._claim(lane)
                if item_id is None:
                    ev = self._wakeups[lane]
                    try:
                        await asyncio.wait_for(ev.wait(), timeout=poll)
                    except TimeoutError:
                        pass
                    ev.clear()
                    continue
                await self._run(item_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — worker must survive anything
                log.exception("queue worker error (lane=%s)", lane)
                await asyncio.sleep(1)

    async def _claim(self, lane: QueueLane) -> int | None:
        async with self._claim_lock:
            async with session_scope() as db:
                item = await db.scalar(
                    select(QueueItem)
                    .where(
                        QueueItem.state == QueueState.pending,
                        QueueItem.lane == lane,
                        (QueueItem.scheduled_at.is_(None))
                        | (QueueItem.scheduled_at <= utcnow()),
                    )
                    .order_by(QueueItem.id)
                    .limit(1)
                )
                if item is None:
                    return None
                item.state = QueueState.running
                item.attempts += 1
                item.started_at = utcnow()
                await db.commit()
                return item.id

    async def _run(self, item_id: int) -> None:
        async with session_scope() as db:
            item = await db.get(QueueItem, item_id)
            if item is None:
                return
            stage, args = item.stage, dict(item.args)
            session_id, job_id, attempts, max_attempts = (
                item.session_id, item.job_id, item.attempts, item.max_attempts,
            )

        error: str | None = None
        try:
            # Stage functions open their own DB scope and record
            # session-level failures themselves (without raising).
            await STAGES[stage](**args)
        except Exception as e:  # noqa: BLE001 — unexpected boundary
            log.exception("queue item %s stage %s crashed", item_id, stage)
            error = f"{type(e).__name__}: {e}"

        async with session_scope() as db:
            item = await db.get(QueueItem, item_id)
            if item is None:
                return
            # A stage may also have recorded a session-level failure
            # (LLM/network errors are caught inside stages) — both kinds
            # go through the same retry policy.
            session = await db.get(Session, session_id) if session_id else None
            stage_failed = session is not None and session.status == SessionStatus.failed
            failure = error or (session.error if stage_failed else None)

            # Attempts never shadow one another: each finished attempt is
            # appended to the item's log for the timeline.
            item.attempt_log = [
                *item.attempt_log,
                {
                    "attempt": attempts,
                    "started_at": item.started_at.isoformat() if item.started_at else None,
                    "finished_at": utcnow().isoformat(),
                    "error": failure,
                },
            ]

            if (error is not None or stage_failed) and attempts < max_attempts:
                delay = get_settings().queue.retry_delay_seconds
                item.state = QueueState.pending  # delayed retry
                item.error = failure
                item.scheduled_at = utcnow() + timedelta(seconds=delay)
                await db.commit()
                if session_id is not None:
                    bus.publish(session_id, "retry_scheduled",
                                attempts=attempts, max_attempts=max_attempts)
                return
            if error is not None or stage_failed:
                item.state = QueueState.failed
                item.error = failure
                if session is not None and session.status != SessionStatus.failed:
                    session.status = SessionStatus.failed
                    session.error = failure
            else:
                item.state = QueueState.done
                item.error = None
            item.finished_at = utcnow()
            await db.commit()
            if job_id is not None:
                await _update_job(db, job_id)


# Serializes job-counter updates: two workers finishing items of the
# same job concurrently would otherwise race recompute-and-write (the
# stale "running" write can land after the "completed" one).
_job_update_lock = asyncio.Lock()


async def _update_job(db: AsyncSession, job_id: int) -> None:
    """Recompute campaign counters from its queue items. A document's
    pipeline may enqueue follow-up items (gate -> analysis); a session
    counts as finished when it has no unfinished items left."""
    async with _job_update_lock:
        await _update_job_locked(db, job_id)


async def _update_job_locked(db: AsyncSession, job_id: int) -> None:
    job = await db.get(Job, job_id)
    if job is None:
        return
    counts = dict(
        (
            await db.execute(
                select(QueueItem.state, func.count())
                .where(QueueItem.job_id == job_id)
                .group_by(QueueItem.state)
            )
        ).all()
    )
    unfinished = counts.get(QueueState.pending, 0) + counts.get(QueueState.running, 0)
    job.failed = counts.get(QueueState.failed, 0)
    job.done = counts.get(QueueState.done, 0)
    if job.status not in (JobStatus.cancelled,):
        if unfinished:
            job.status = JobStatus.running
        else:
            job.status = JobStatus.completed if job.failed == 0 else (
                JobStatus.failed if job.done == 0 else JobStatus.completed
            )
    await db.commit()


async def recover() -> dict[str, int]:
    """Startup recovery.

    - queue items left ``running`` by a crash: retry (back to pending)
      or fail when attempts are exhausted;
    - in-flight sessions WITHOUT any pending/running queue item are
      orphans from the pre-queue era (or lost rows): mark failed.
    """
    async with session_scope() as db:
        retried = failed = orphaned = 0
        running_items = (
            await db.scalars(select(QueueItem).where(QueueItem.state == QueueState.running))
        ).all()
        for item in running_items:
            item.attempt_log = [
                *item.attempt_log,
                {
                    "attempt": item.attempts,
                    "started_at": item.started_at.isoformat() if item.started_at else None,
                    "finished_at": None,
                    "error": "interrupted by app restart",
                },
            ]
            if item.attempts < item.max_attempts:
                item.state = QueueState.pending
                item.scheduled_at = None
                retried += 1
            else:
                item.state = QueueState.failed
                item.error = (item.error or "") + " [interrupted by restart]"
                failed += 1
                if item.session_id:
                    session = await db.get(Session, item.session_id)
                    if session is not None:
                        session.status = SessionStatus.failed
                        session.error = "interrupted: retries exhausted after restart"

        active = (
            await db.scalars(
                select(Session).where(
                    Session.phase.in_(
                        [SessionPhase.queued, SessionPhase.ocr_running, SessionPhase.analyzing]
                    )
                    | (Session.status == SessionStatus.running),
                    Session.status != SessionStatus.failed,
                )
            )
        ).all()
        for session in active:
            has_work = await db.scalar(
                select(func.count())
                .select_from(QueueItem)
                .where(
                    QueueItem.session_id == session.id,
                    QueueItem.state.in_([QueueState.pending, QueueState.running]),
                )
            )
            if not has_work:
                session.status = SessionStatus.failed
                session.error = "interrupted: the app restarted while this stage was running"
                orphaned += 1
        await db.commit()
        return {"retried": retried, "failed": failed, "orphaned": orphaned}


workers = QueueWorkers()
