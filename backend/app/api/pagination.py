"""One pagination implementation for every list route.

The routes keep their typed ``*Page`` response models (the envelope
shape is the API contract); this owns the clamping and windowing so the
rules can never diverge between routes again.
"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

MAX_PAGE_SIZE = 200


class PageWindow:
    """Clamped page/window plus the total count for one list query."""

    def __init__(self, page: int, page_size: int, count: int):
        self.page = page
        self.page_size = page_size
        self.count = count


async def paginate(
    db: AsyncSession,
    stmt: Select,
    count_stmt: Select,
    *,
    page: int,
    page_size: int,
    max_page_size: int = MAX_PAGE_SIZE,
) -> tuple[PageWindow, Select]:
    """Clamp the window, run the count, and return the windowed
    statement. ``count_stmt`` is separate because list statements may
    join/group for display columns that must not affect the count."""
    page = max(1, page)
    page_size = min(max_page_size, max(1, page_size))
    count = (await db.scalar(count_stmt)) or 0
    window = stmt.offset((page - 1) * page_size).limit(page_size)
    return PageWindow(page, page_size, count), window


def count_of(entity, *where) -> Select:
    return select(func.count()).select_from(entity).where(*where)
