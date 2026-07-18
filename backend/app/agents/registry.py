"""Agent definitions. One entity at a time, capped iterations, shared
toolset; scope is set by the per-kind system prompt.

Prompt composition: system-supplied BASE (user-overridable via
Settings, because small local models often need per-setup tuning)
+ per-kind TASK + optional user ADDITION.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable

from pydantic_ai import Agent, ModelRetry, RunContext

from app.agents.deps import AgentDeps
from app.agents.tools import DOCUMENT_AGENT_TOOLS, TAXONOMY_AGENT_TOOLS
from app.db.models import AgentKind
from app.llm.factory import agent_model, agent_model_settings
from app.services.events import bus


def _instrumented(fn: Callable) -> Callable:
    """Wrap a tool for (a) live SSE events (call AND result — the
    streaming UI renders the same tool rows as the finished one) and
    (b) serialized execution: pydantic-ai runs parallel tool calls
    concurrently, but all tools of a run share one DB session."""

    @functools.wraps(fn)
    async def wrapper(ctx: RunContext[AgentDeps], *args, **kwargs):
        try:
            args_preview = json.dumps(kwargs, default=str)[:500]
        except TypeError:
            args_preview = ""
        bus.publish(
            ctx.deps.session_id, "step_progress",
            step_id=ctx.deps.step_id, tool=fn.__name__, args=args_preview,
        )
        try:
            async with ctx.deps.tool_lock:
                result = await fn(ctx, *args, **kwargs)
        except ModelRetry as e:
            bus.publish(
                ctx.deps.session_id, "step_progress",
                step_id=ctx.deps.step_id, tool_done=fn.__name__,
                result=str(e)[:500], rejected=True,
            )
            raise
        except Exception as e:
            # AUDIT BC-F13: a hard tool failure still terminates the
            # live UI's tool row — without this the row spins until the
            # step_changed failure invalidation arrives.
            bus.publish(
                ctx.deps.session_id, "step_progress",
                step_id=ctx.deps.step_id, tool_done=fn.__name__,
                result=f"error: {type(e).__name__}: {e}"[:500], rejected=True,
            )
            raise
        try:
            result_preview = (
                result if isinstance(result, str) else json.dumps(result, default=str)
            )[:500]
        except TypeError:
            result_preview = ""
        extra: dict = {}
        if fn.__name__.startswith("propose_") and isinstance(result, str):
            from app.services.transcript import _PROPOSAL_TOKEN_RE

            m = _PROPOSAL_TOKEN_RE.search(result)
            if m:
                extra["proposal_id"] = int(m.group(1))
        bus.publish(
            ctx.deps.session_id, "step_progress",
            step_id=ctx.deps.step_id, tool_done=fn.__name__,
            result=result_preview, rejected=False, **extra,
        )
        return result

    return wrapper


# The system-supplied base prompt. Reviewed for small-model adherence:
# numbered absolute rules, one concept per rule, explicit good/bad
# examples for the formats that matter.
DEFAULT_BASE_PROMPT = """\
You are part of paperless-llm, an assistant that curates a
paperless-ngx document archive. The archive is multilingual (mostly
German and English): work language-agnostically and never translate
document content.

THESE RULES ARE ABSOLUTE:

1. ONE proposal per turn. A proposal may cover several fields of one
   target (title + tags + correspondent in a single
   propose_update_document_metadata is ONE proposal), but never call a
   second propose_* tool in the same turn. Foundational changes first:
   if a needed entity does not exist yet, propose creating it THIS
   turn and leave changes that depend on it for a later turn. After
   the user decides on your proposal you automatically get a follow-up
   turn telling you their decision; then propose the next single
   change, or finish.

2. Proposals are the ONLY way to change anything. Your prose never
   changes data. If a change is needed — including revising one of
   your own earlier proposals — you MUST call the propose_* tool with
   the full new values; it automatically supersedes your earlier
   proposal for the same target.

3. REFERENCE TOKENS. Whenever your prose mentions a document, tag,
   correspondent, document type, storage path, or proposal, write it
   as a token with its NUMERIC id — the UI renders tokens as clickable
   chips with the real name:
     [[document:13]] [[tag:5]] [[correspondent:2]] [[document_type:4]]
     [[storage_path:1]] [[proposal:9]]
   GOOD: "Assigned [[tag:5]] and set [[correspondent:2]] on
   [[document:13]]."
   BAD: "Assigned tag 5 (steuer) to document 13." / "correspondent
   ID 7" / "#5" / "[[tag:steuer]]".
   Never write raw ids and never put names inside tokens. Do not
   repeat the name after a token — the chip already shows it.
   GOOD: remove [[tag:5]].
   BAD: remove [[tag:5]] ("old-stuff-2019").
   BAD: set [[document_type:2]] Invoice.

4. `user_instructions` attached to an entity are BINDING. Always obey
   them when assigning, removing, or otherwise handling that entity.

5. Verify before proposing: referenced ids must exist (check with the
   list_*/search tools). Prefer assigning an existing entity over
   creating a near-duplicate ("Telarko Deutschland GmbH" maps to an
   existing "Telarko"). No-op proposals are rejected.

6. End EVERY turn with a few short closing sentences (reference
   tokens included). Answer plainly: no "Summary" heading, no
   "Summary:" lead-in — just say what you did and what comes next.

FORMATTING: your prose is rendered as Markdown (GitHub flavor). You
may use paragraphs, **bold**, *italics*, `inline code`, bullet and
numbered lists, tables, blockquotes, and fenced code blocks. Do not
use headings (the UI provides structure) or links other than the
[[type:id]] reference tokens.
"""

_DOCUMENT_TASK = """
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
"""

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

_TASKS: dict[AgentKind, str] = {AgentKind.document: _DOCUMENT_TASK}
for _kind, _noun, _plural in (
    (AgentKind.tag, "tag", "tags"),
    (AgentKind.correspondent, "correspondent", "correspondents"),
    (AgentKind.document_type, "document type", "document_types"),
):
    _TASKS[_kind] = _TAXONOMY_TASK.format(noun=_noun, plural=_plural)


def compose_prompt(
    kind: AgentKind, *, base: str = "", addition: str = ""
) -> str:
    """BASE (user override or system default) + per-kind task +
    optional user addition."""
    if kind not in _TASKS:
        raise ValueError(f"agent kind {kind} not implemented")
    prompt = (base.strip() or DEFAULT_BASE_PROMPT) + _TASKS[kind]
    if addition.strip():
        prompt += (
            "\nAdditional instructions from the archive's owner "
            "(follow them):\n" + addition.strip() + "\n"
        )
    return prompt


def build_agent(
    kind: AgentKind, *, base: str = "", addition: str = ""
) -> Agent[AgentDeps, str]:
    tools = DOCUMENT_AGENT_TOOLS if kind == AgentKind.document else TAXONOMY_AGENT_TOOLS
    return Agent(
        agent_model(),
        deps_type=AgentDeps,
        system_prompt=compose_prompt(kind, base=base, addition=addition),
        tools=[_instrumented(t) for t in tools],
        model_settings=agent_model_settings(),
        retries=2,
    )
