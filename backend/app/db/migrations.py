"""Programmatic alembic upgrade at app startup."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.config import get_settings

log = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def _sync_url(url: str) -> str:
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def _run(database_url: str) -> None:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _sync_url(database_url))
    command.upgrade(cfg, "head")


async def run_migrations() -> None:
    await asyncio.to_thread(_run, get_settings().database_url)
