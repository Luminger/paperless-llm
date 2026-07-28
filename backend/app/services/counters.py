"""Lifetime counters — atomic increments, race-free across workers."""

from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Counter

log = logging.getLogger(__name__)


async def increment(db: AsyncSession, **deltas: int) -> None:
    """Add deltas to named counters (creating them on first use). Never
    raises — stats must not break the operation they measure."""
    try:
        for key, delta in deltas.items():
            if not delta:
                continue
            result = await db.execute(
                update(Counter).where(Counter.key == key).values(value=Counter.value + delta)
            )
            if result.rowcount == 0:
                # AUDIT SV-M4: the insert race must roll back to a
                # SAVEPOINT, not poison the caller's transaction — a
                # bare flush failure would make every later statement
                # of the caller raise PendingRollbackError (recording a
                # successful agent turn as a failed step).
                try:
                    async with db.begin_nested():
                        db.add(Counter(key=key, value=delta))
                        await db.flush()
                except IntegrityError:
                    await db.execute(
                        update(Counter)
                        .where(Counter.key == key)
                        .values(value=Counter.value + delta)
                    )
        await db.flush()
    except Exception:  # noqa: BLE001
        log.exception("counter increment failed (%s)", list(deltas))


async def get_all(db: AsyncSession) -> dict[str, int]:
    rows = (await db.scalars(select(Counter))).all()
    return {c.key: c.value for c in rows}
