"""Agent definitions. One entity at a time, capped iterations, shared
toolset; scope is set by the per-kind system prompt.

M1 ships the DocumentAgent; Tag/Correspondent/DocumentType agents land
in M3. (A freestyle explorer agent is deliberately out of scope — see
DESIGN.md "Possible future extensions".)
"""

from __future__ import annotations

import functools
from collections.abc import Callable

from pydantic_ai import Agent, RunContext

from app.agents.deps import AgentDeps
from app.agents.tools import DOCUMENT_AGENT_TOOLS, TAXONOMY_AGENT_TOOLS
from app.db.models import AgentKind
from app.llm.factory import agent_model, agent_model_settings
from app.services.events import bus


def _instrumented(fn: Callable) -> Callable:
    """Publish a tool_called event before each tool invocation — SSE
    progress feedback during long agent runs. We deliberately do NOT use
    pydantic-ai's event_stream_handler: it forces the model request into
    streaming mode, which the qwen3_xml parser can't do reliably."""

    @functools.wraps(fn)
    async def wrapper(ctx: RunContext[AgentDeps], *args, **kwargs):
        bus.publish(ctx.deps.session_id, "tool_called", tool=fn.__name__)
        return await fn(ctx, *args, **kwargs)

    return wrapper

_COMMON = """\
You are part of paperless-llm, an assistant for a paperless-ngx document
archive. The archive contains documents in multiple languages (mostly
German and English); always work language-agnostically and never
translate document contents.

Hard rules:
- You can NEVER modify paperless directly. All changes go through
  propose_* tools and are reviewed by a human. Emit at most one proposal
  per distinct change; never repeat a proposal you already recorded.
- Before referencing or creating tags/correspondents/document types,
  check what already exists (list_* tools). Prefer assigning an existing
  entity over creating a near-duplicate (e.g. "Telarko Deutschland GmbH"
  should map to an existing "Telarko").
- Be economical with tool calls; you have a limited budget per run.
- Finish with a short plain-text summary of what you found and proposed.
"""

_PROMPTS: dict[AgentKind, str] = {
    AgentKind.document: _COMMON + """
Your task: process ONE document (the user message states which). The
document's OCR content has already been handled before you run — treat
the stored content as the source of truth; never attempt to re-OCR or
rewrite it. Steps:
1. Fetch the document (get_document) and read its content.
2. Determine correct metadata from the content: title (concise,
   descriptive, in the document's language), correspondent (the OTHER
   party, not the archive owner), document type, tags (only genuinely
   applicable ones), and the creation date printed on the document.
3. Compare with current metadata. Propose only actual changes via
   propose_update_document_metadata. If a clearly needed entity is
   missing, first check find_similar_entities — only propose_create_entity
   (with assign_to_documents) when nothing close exists.
""",
}

_TAXONOMY_TASK = """
Your task: review ONE {noun} of the archive's taxonomy (the user message
states which). Steps:
1. Look the entity up (list_{plural}) — name, matching rule, document
   count. Sample a few of its documents (search_documents) if usage is
   unclear.
2. Hunt duplicates: find_similar_entities with the entity's name. High
   similarity plus overlapping meaning ⇒ the entities should be merged.
3. Judge the name itself: typos, inconsistent casing, junk (scanner
   artifacts), overly specific or meaningless labels.
Then propose AT MOST what is warranted:
- propose_update_entity: rename or fix the matching rule.
- propose_merge_entities: merge the WORSE-named/smaller entity (source)
  INTO the canonical one (target); the target survives. When reviewing
  entity X and it duplicates a better entity Y, source=X, target=Y.
- propose_delete_entity: only for empty or nonsense entries.
If the entity is fine as it is, finish WITHOUT a proposal and say so.
"""

for _kind, _noun, _plural in (
    (AgentKind.tag, "tag", "tags"),
    (AgentKind.correspondent, "correspondent", "correspondents"),
    (AgentKind.document_type, "document type", "document_types"),
):
    _PROMPTS[_kind] = _COMMON + _TAXONOMY_TASK.format(noun=_noun, plural=_plural)


def build_agent(kind: AgentKind) -> Agent[AgentDeps, str]:
    if kind not in _PROMPTS:
        raise ValueError(f"agent kind {kind} not implemented")
    tools = DOCUMENT_AGENT_TOOLS if kind == AgentKind.document else TAXONOMY_AGENT_TOOLS
    return Agent(
        agent_model(),
        deps_type=AgentDeps,
        system_prompt=_PROMPTS[kind],
        tools=[_instrumented(t) for t in tools],
        model_settings=agent_model_settings(),
        retries=2,
    )
