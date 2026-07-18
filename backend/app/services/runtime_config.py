"""Runtime configuration overrides — the Settings UI's config layer.

Values live in the DB (one JSON blob under a reserved prefs key) and
are applied as a pydantic-settings source between the environment and
the config file: env > UI > file > defaults. Only whitelisted keys
(``config.EDITABLE_KEYS``) ever land here, and keys the environment
sets are refused — the environment is authoritative.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import set_runtime_overrides
from app.db.models import UserPref
from app.db.session import session_scope

log = logging.getLogger(__name__)

_KEY = "_config.overrides"  # reserved: never surfaced by the prefs API


async def load_overrides(db: AsyncSession) -> dict[str, object]:
    row = await db.scalar(select(UserPref).where(UserPref.key == _KEY))
    if row is None:
        return {}
    try:
        data = json.loads(row.value)
    except ValueError:
        log.warning("stored config overrides are not valid JSON — ignoring")
        return {}
    return data if isinstance(data, dict) else {}


async def save_overrides(db: AsyncSession, values: dict[str, object]) -> None:
    row = await db.scalar(select(UserPref).where(UserPref.key == _KEY))
    payload = json.dumps(values)
    if row is None:
        db.add(UserPref(key=_KEY, value=payload))
    else:
        row.value = payload
    await db.flush()


async def init_from_db() -> None:
    """Startup: the persisted UI overrides become the active layer."""
    async with session_scope() as db:
        overrides = await load_overrides(db)
    set_runtime_overrides(overrides)
    if overrides:
        log.info("applied %d runtime config override(s) from the DB", len(overrides))
