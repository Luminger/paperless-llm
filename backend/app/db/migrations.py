"""Programmatic alembic upgrade at app startup.

Legacy databases (created via ``create_all`` before alembic landed) are
detected by having app tables but no ``alembic_version`` — they get
stamped with the baseline revision first, then upgraded normally.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from app.config import get_settings

log = logging.getLogger(__name__)

# The m1-m2 baseline revision (schema as of the first release).
BASELINE_REVISION = "be2f530fc15c"

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def _sync_url(url: str) -> str:
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def _run(database_url: str) -> None:
    url = _sync_url(database_url)
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            insp = inspect(conn)
            legacy = insp.has_table("sessions") and not insp.has_table("alembic_version")
    finally:
        engine.dispose()

    if legacy:
        log.warning("pre-alembic database detected; stamping baseline %s", BASELINE_REVISION)
        command.stamp(cfg, BASELINE_REVISION)
    command.upgrade(cfg, "head")


async def run_migrations() -> None:
    await asyncio.to_thread(_run, get_settings().database_url)
