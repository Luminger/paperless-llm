"""In-process pub/sub bus for session event streams (SSE).

Design: events are an INVALIDATION SIGNAL, not a data transport — they
carry a type and a few identifiers, and the frontend reacts by
refetching over the normal REST API. One serialization path, reconnects
are trivially safe (worst case: an extra refetch).

Single-process asyncio for now; the M4 queue work swaps this for a
redis-backed bus behind the same three methods.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def subscribe(self, session_id: int) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subs[session_id].add(q)
        return q

    def unsubscribe(self, session_id: int, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs[session_id].discard(q)
        if not self._subs[session_id]:
            del self._subs[session_id]

    def publish(self, session_id: int, type_: str, **data: Any) -> None:
        event = {
            "type": type_,
            "session_id": session_id,
            "ts": datetime.now(UTC).isoformat(),
            **data,
        }
        for q in list(self._subs.get(session_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop. The signal is redundant with REST
                # state, so a dropped event only delays a refetch.
                pass


bus = EventBus()
