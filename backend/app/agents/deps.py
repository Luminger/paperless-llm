"""Dependencies injected into every agent run (pydantic-ai deps_type)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Proposal
from app.paperless import PaperlessClient


@dataclass
class AgentDeps:
    paperless: PaperlessClient
    db: AsyncSession
    settings: Settings
    session_id: int
    # Proposals emitted during the current run (via propose_* tools).
    emitted: list[Proposal] = field(default_factory=list)
    # Per-run cache of taxonomy listings (validation lookups).
    taxonomy_cache: dict = field(default_factory=dict)

    async def taxonomy(self, entity_type: str) -> list:
        if entity_type not in self.taxonomy_cache:
            fetch = {
                "tag": self.paperless.list_tags,
                "correspondent": self.paperless.list_correspondents,
                "document_type": self.paperless.list_document_types,
                "storage_path": self.paperless.list_storage_paths,
            }[entity_type]
            self.taxonomy_cache[entity_type] = await fetch()
        return self.taxonomy_cache[entity_type]

    @property
    def max_chars(self) -> int:
        """Approximate character clamp for tool results derived from the
        configured input-token budget (≈4 chars/token, /4 so a single
        tool result can't consume the whole context)."""
        return self.settings.llm.agent.max_input_tokens


def clamp_text(text: str, max_chars: int, note: str = "") -> str:
    if len(text) <= max_chars:
        return text
    kept = text[:max_chars]
    suffix = f"\n\n[... truncated, {len(text) - max_chars} characters omitted{note}]"
    return kept + suffix
