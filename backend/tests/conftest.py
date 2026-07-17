from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base

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
async def paperless_client():
    """Client pointed at the respx-mocked base URL."""
    from app.paperless import PaperlessClient

    async with PaperlessClient(PAPERLESS_URL, "test-token") as client:
        yield client
