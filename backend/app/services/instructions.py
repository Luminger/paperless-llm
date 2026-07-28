"""App-local per-entity agent instructions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EntityInstruction

# Seeded once for inbox tags (only when no row exists at all — clearing
# leaves an empty row so the default never comes back).
INBOX_DEFAULT = (
    "This is the inbox tag. Remove it from every document you analyze — "
    "analyzed documents should no longer occupy the inbox."
)


async def get_map(db: AsyncSession, entity_type: str) -> dict[int, str]:
    rows = (
        await db.scalars(
            select(EntityInstruction).where(EntityInstruction.entity_type == entity_type)
        )
    ).all()
    return {r.entity_id: r.instructions for r in rows if r.instructions}


async def set_instructions(
    db: AsyncSession, entity_type: str, entity_id: int, text: str
) -> None:
    row = await db.scalar(
        select(EntityInstruction).where(
            EntityInstruction.entity_type == entity_type,
            EntityInstruction.entity_id == entity_id,
        )
    )
    if row is None:
        db.add(
            EntityInstruction(
                entity_type=entity_type, entity_id=entity_id, instructions=text
            )
        )
    else:
        row.instructions = text
    await db.commit()


async def ensure_inbox_defaults(db: AsyncSession, tags: list[Any]) -> None:
    """Seed the inbox default for inbox tags that never had a row."""
    inbox = [t for t in tags if getattr(t, "is_inbox_tag", False)]
    if not inbox:
        return
    existing = set(
        (
            await db.scalars(
                select(EntityInstruction.entity_id).where(
                    EntityInstruction.entity_type == "tag",
                    EntityInstruction.entity_id.in_([t.id for t in inbox]),
                )
            )
        ).all()
    )
    changed = False
    for t in inbox:
        if t.id not in existing:
            db.add(
                EntityInstruction(
                    entity_type="tag", entity_id=t.id, instructions=INBOX_DEFAULT
                )
            )
            changed = True
    if changed:
        await db.commit()
