"""Dependencies injected into every agent run (pydantic-ai deps_type)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Proposal
from app.paperless import PaperlessClient
from app.paperless.taxonomy import TAXONOMY


@dataclass
class AgentDeps:
    paperless: PaperlessClient
    db: AsyncSession
    settings: Settings
    session_id: int
    # The step whose run this is — stamps proposals and progress events.
    step_id: int | None = None
    # Proposals emitted during the current run (via propose_* tools).
    emitted: list[Proposal] = field(default_factory=list)
    # Per-run cache of taxonomy listings (validation lookups).
    taxonomy_cache: dict = field(default_factory=dict)
    # Serializes tool execution within a run: pydantic-ai executes
    # parallel tool calls concurrently, but they share this one DB
    # session — concurrent flush/commit corrupts it
    # (IllegalStateChangeError, seen live with find_similar_entities
    # committing embedding cache rows next to another tool).
    tool_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def taxonomy(self, entity_type: str) -> list:
        if entity_type not in self.taxonomy_cache:
            self.taxonomy_cache[entity_type] = await TAXONOMY[entity_type].list(
                self.paperless
            )
        return self.taxonomy_cache[entity_type]

    async def custom_fields(self) -> list:
        """Per-run cache of the custom-field registry (same contract as
        taxonomy(): names/types resolve once, validation lookups are
        free afterwards)."""
        if "__custom_fields__" not in self.taxonomy_cache:
            self.taxonomy_cache["__custom_fields__"] = (
                await self.paperless.list_custom_fields()
            )
        return self.taxonomy_cache["__custom_fields__"]

    @property
    def max_chars(self) -> int:
        """Character clamp for a SINGLE tool result: token budget × ≈4
        chars/token, ÷4 so one result can't consume the whole context —
        net: max_input_tokens characters. AUDIT BC-F14: the division
        lives HERE and nowhere else (callers used to divide again,
        clamping reads to 1/16 of the budget — long documents became
        unreadable within the tool-iteration cap)."""
        return self.settings.llm.agent.max_input_tokens


def clamp_text(text: str, max_chars: int, note: str = "") -> str:
    if len(text) <= max_chars:
        return text
    kept = text[:max_chars]
    suffix = f"\n\n[... truncated, {len(text) - max_chars} characters omitted{note}]"
    return kept + suffix
