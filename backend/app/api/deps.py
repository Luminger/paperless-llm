"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.config import get_settings
from app.paperless import PaperlessClient


async def get_paperless() -> AsyncIterator[PaperlessClient]:
    s = get_settings().paperless
    async with PaperlessClient(
        s.base_url,
        s.token,
        timeout=s.timeout_seconds,
        username=s.username,
        password=s.password,
    ) as client:
        yield client
