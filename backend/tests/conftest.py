from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Unit tests must not read the developer's local paperless-llm.toml —
# the config-file layer is exercised explicitly where it matters.
os.environ.setdefault("PAPERLESS_LLM_CONFIG", "/nonexistent/paperless-llm.toml")

from app.config import reset_settings_cache  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.session import dispose_engine, init_db  # noqa: E402

pytest_plugins = ["tests.paperless_fixtures"]

PAPERLESS_URL = "http://paperless.test"


@pytest.fixture
async def db():
    """Fresh in-memory database per test."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def file_db(tmp_path, monkeypatch):
    """File-backed sqlite through the APP's global engine — for tests
    whose code under test opens its own sessions (workers, recovery).
    Modules that need extra env (queue tuning) shadow this fixture and
    set it before the settings-cache reset."""
    monkeypatch.setenv("PLLM_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/s.sqlite3")
    reset_settings_cache()
    await dispose_engine()
    await init_db()
    yield
    await dispose_engine()
    reset_settings_cache()


@pytest.fixture
async def paperless_client():
    """Client pointed at the respx-mocked base URL."""
    from app.paperless import PaperlessClient

    async with PaperlessClient(PAPERLESS_URL, "test-token") as client:
        yield client
