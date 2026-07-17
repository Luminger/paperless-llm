"""Event bus: in-process pub/sub used by the SSE endpoint."""

from __future__ import annotations

import asyncio

from app.services.events import EventBus


async def test_publish_reaches_all_subscribers_of_that_session():
    bus = EventBus()
    q1, q2 = bus.subscribe(1), bus.subscribe(1)
    other = bus.subscribe(2)
    bus.publish(1, "phase_changed", phase="done")
    for q in (q1, q2):
        ev = q.get_nowait()
        assert ev["type"] == "phase_changed" and ev["phase"] == "done"
        assert ev["session_id"] == 1 and "ts" in ev
    assert other.empty()


async def test_unsubscribe_stops_delivery_and_cleans_up():
    bus = EventBus()
    q = bus.subscribe(1)
    bus.unsubscribe(1, q)
    bus.publish(1, "x")
    assert q.empty()
    assert not bus._subs  # no leaked empty sets


async def test_slow_consumer_drops_instead_of_blocking():
    bus = EventBus()
    q = bus.subscribe(1)
    for _ in range(300):  # queue maxsize is 256
        bus.publish(1, "tick")
    assert q.qsize() == 256  # overflow dropped, publish never raised


async def test_publish_without_subscribers_is_noop():
    EventBus().publish(99, "x")
    await asyncio.sleep(0)  # nothing scheduled, nothing crashes
