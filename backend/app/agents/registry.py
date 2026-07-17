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
from app.agents.tools import ALL_TOOLS, DOCUMENT_AGENT_TOOLS
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
   missing, propose_create_entity (with assign_to_documents).
""",
}


def build_agent(kind: AgentKind) -> Agent[AgentDeps, str]:
    if kind not in _PROMPTS:
        raise ValueError(f"agent kind {kind} not implemented yet (M3)")
    tools = DOCUMENT_AGENT_TOOLS if kind == AgentKind.document else ALL_TOOLS
    return Agent(
        agent_model(),
        deps_type=AgentDeps,
        system_prompt=_PROMPTS[kind],
        tools=[_instrumented(t) for t in tools],
        model_settings=agent_model_settings(),
        retries=2,
    )
