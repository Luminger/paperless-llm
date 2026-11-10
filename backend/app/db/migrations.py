"""Programmatic alembic upgrade at app startup.

The alembic tree (``alembic.ini`` + ``alembic/``) is resolved relative to
``Settings.root_dir`` — the backend directory in a source checkout and in
the container image, and whatever a packager installs them into (the nix
package sets ``PLLM_ROOT_DIR``). Never relative to ``__file__``: an
installed ``app`` package does not own the migration tree.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.config import get_settings

log = logging.getLogger(__name__)


def _sync_url(url: str) -> str:
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def _run(database_url: str, root_dir: Path) -> None:
    cfg = Config(str(root_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(root_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _sync_url(database_url))
    command.upgrade(cfg, "head")


async def run_migrations() -> None:
    settings = get_settings()
    await asyncio.to_thread(_run, settings.database_url, settings.root_dir)
