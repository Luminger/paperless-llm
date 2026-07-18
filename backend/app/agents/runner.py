"""Runs one agent turn against a persisted Session: loads history,
executes with the endpoint semaphore and iteration cap, persists new
history, and finalizes emitted proposals (draft -> pending, superseding
older open revisions for the same target).

Lifecycle (state, retries, events) is the step engine's job — this
module only executes the turn and raises on failure.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.usage import UsageLimits
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.deps import AgentDeps
from app.agents.registry import build_agent
from app.config import get_settings
from app.db.models import Proposal, ProposalStatus, Session, Step
from app.llm.factory import llm_semaphore
from app.paperless import PaperlessClient
from app.services.events import bus

log = logging.getLogger(__name__)

@dataclass
class RunOutcome:
    session_id: int
    output: str
    proposal_ids: list[int] = field(default_factory=list)
    # Slice of session.message_history this turn appended.
    message_range: tuple[int, int] = (0, 0)


def _load_history(session: Session) -> list[ModelMessage]:
    if not session.message_history:
        return []
    return ModelMessagesTypeAdapter.validate_python(session.message_history)


def _dump_history(messages: list[ModelMessage]) -> list:
    return ModelMessagesTypeAdapter.dump_python(messages, mode="json")


def _progress_handler(session_id: int, step_id: int | None):
    """Streaming-run consumer: throttled step_progress SSE (streamed
    chunks arrive roughly one token each, so the delta-event count is an
    honest token proxy; visible text is forwarded as a tail preview).
    Passing a handler is also what switches pydantic-ai to streamed
    model requests — only used when the profile declares
    supports_streaming."""
    from pydantic_ai.messages import (
        PartStartEvent,
        TextPart,
        TextPartDelta,
        ThinkingPart,
        ThinkingPartDelta,
    )

    async def handler(ctx, events) -> None:
        tokens = 0
        # part index -> (kind, accumulated content); the live UI renders
        # the SAME items the finished transcript shows.
        parts: dict[int, list] = {}
        dirty: set[int] = set()
        last_publish = 0.0

        def flush(now: float) -> None:
            nonlocal last_publish
            for i in sorted(dirty):
                kind, content = parts[i]
                bus.publish(
                    session_id, "step_progress",
                    step_id=step_id, part=i, part_kind=kind,
                    content=content[-6000:], tokens=tokens,
                )
            dirty.clear()
            last_publish = now

        async for ev in events:
            if isinstance(ev, PartStartEvent):
                if isinstance(ev.part, ThinkingPart):
                    parts[ev.index] = ["thinking", ev.part.content or ""]
                    dirty.add(ev.index)
                elif isinstance(ev.part, TextPart):
                    parts[ev.index] = ["text", ev.part.content or ""]
                    dirty.add(ev.index)
            delta = getattr(ev, "delta", None)
            if delta is not None:
                tokens += 1
                if isinstance(delta, (TextPartDelta, ThinkingPartDelta)) and ev.index in parts:
                    parts[ev.index][1] += delta.content_delta or ""
                    dirty.add(ev.index)
            now = time.monotonic()
            if dirty and now - last_publish >= 1.0:
                flush(now)
        flush(time.monotonic())

    return handler


def _steering_preamble(session: Session, db_proposals: list[Proposal]) -> str | None:
    """If the user hand-edited an open proposal, tell the agent (DESIGN.md:
    user edits are injected into context so 'fix it yourself' and 'agent,
    fix it' compose)."""
    edited = [
        p for p in db_proposals
        if p.user_payload is not None and p.status == ProposalStatus.pending
    ]
    if not edited:
        return None
    lines = ["Note: the user has manually amended your proposal(s):"]
    for p in edited:
        lines.append(f"- proposal #{p.id} ({p.kind}) is now: {p.user_payload}")
    lines.append("Take these amendments as the current state of the proposals.")
    return "\n".join(lines)


async def run_agent_turn(
    paperless: PaperlessClient,
    db: AsyncSession,
    session: Session,
    user_message: str,
    agent: Agent[AgentDeps, str] | None = None,
    step: Step | None = None,
) -> RunOutcome:
    """Execute one turn. ``agent`` is injectable for tests (TestModel /
    FunctionModel); defaults to the configured kind. Raises on failure —
    the step engine records it (proposals drafted before the failure are
    kept reviewable)."""
    settings = get_settings()
    profile = settings.llm.agent
    from app.services.prefs import format_instructions, get_prefs

    prefs = await get_prefs(db)
    agent = agent or build_agent(
        session.agent_kind,
        base=prefs.get("agent_prompt_base", ""),
        addition=prefs.get("agent_prompt_addition", ""),
    )

    deps = AgentDeps(
        paperless=paperless,
        db=db,
        settings=settings,
        session_id=session.id,
        step_id=step.id if step is not None else None,
    )

    open_proposals = list(
        (
            await db.scalars(select(Proposal).where(Proposal.session_id == session.id))
        ).all()
    )
    # The steering preamble (user hand-edits) travels as run-time
    # instructions, NOT prompt prefix — keeps the stored user message
    # (and thus the derived transcript) clean.
    preamble = _steering_preamble(session, open_proposals)
    # The model writes dates/times the way the user reads them — the UI
    # and the agent must agree on formatting.
    fmt = format_instructions(prefs)
    preamble = f"{preamble}\n\n{fmt}" if preamble else fmt
    if history_exists := bool(session.message_history):
        # Follow-up turns: promises don't change proposals — tools do.
        followup = (
            "This is a follow-up turn. If you conclude that something "
            "should change, you MUST call the appropriate propose_* tool — "
            "a textual reply alone never creates, changes, or withdraws a "
            "proposal. A new proposal for the same target automatically "
            "supersedes your earlier one. Propose at most the SINGLE most "
            "important next change; only reply without a tool call when "
            "nothing needs to change anymore."
        )
        preamble = f"{preamble}\n\n{followup}" if preamble else followup
    del history_exists
    history_start = len(session.message_history or [])

    async def _run(stream: bool):
        async with llm_semaphore(profile.base_url, profile.max_concurrent):
            return await agent.run(
                user_message,
                deps=deps,
                instructions=preamble,
                message_history=_load_history(session),
                usage_limits=UsageLimits(request_limit=profile.max_tool_iterations),
                event_stream_handler=(
                    _progress_handler(session.id, deps.step_id) if stream else None
                ),
            )

    try:
        try:
            result = await _run(stream=profile.supports_streaming)
        except IndexError:
            if not profile.supports_streaming:
                raise
            # pydantic-ai's streaming part tracker (part_end_event) indexes
            # into get_parts(), which FILTERS ToolCallPartDeltas — certain
            # vLLM tool-call stream shapes make the list shorter than the
            # tracked index (verified through 2.13.0; we pin 2.12.0 —
            # re-check when bumping). Deterministic per
            # response, so step retries can't help — but the non-streaming
            # path never touches that code. Trade live tokens for a
            # completed turn.
            log.warning(
                "streaming part-tracking bug (pydantic-ai part_end_event "
                "IndexError); re-running the turn without streaming"
            )
            # The aborted attempt may already have executed tools —
            # including a propose_* call. Those drafts have no transcript
            # provenance (only the re-run's messages are persisted) and
            # would trip the one-proposal-per-turn guard on the re-run,
            # so discard them: the re-run re-proposes if still warranted.
            for p in deps.emitted:
                await db.delete(p)
            deps.emitted.clear()
            # COMMIT, don't just flush (reinspection): _persist committed
            # these drafts, so if the re-run fails AND the failure path's
            # promotion commit also fails, a mere flush would roll the
            # deletes back and strand the aborted attempt's drafts as
            # permanent orphan rows.
            await db.commit()
            result = await _run(stream=False)
    except Exception:
        # Keep any proposals drafted before the failure reviewable. A
        # tool-level DB error may have poisoned the session — in that
        # case commit raises PendingRollbackError; roll back and retry
        # the promotion on a clean transaction. Whatever happens here,
        # the ORIGINAL exception is the one that propagates.
        draft_ids = [p.id for p in deps.emitted if p.id is not None]
        try:
            for p in deps.emitted:
                p.status = ProposalStatus.pending
            await db.commit()
        except Exception:
            await db.rollback()
            try:
                if draft_ids:
                    await db.execute(
                        update(Proposal)
                        .where(
                            Proposal.id.in_(draft_ids),
                            Proposal.status == ProposalStatus.draft,
                        )
                        .values(status=ProposalStatus.pending)
                    )
                    await db.commit()
            except Exception:
                log.exception(
                    "could not promote draft proposals after turn failure"
                )
                await db.rollback()
        raise

    session.message_history = _dump_history(result.all_messages())
    if not session.title:
        session.title = user_message[:200]

    from app.services.counters import increment

    usage = result.usage() if callable(result.usage) else result.usage
    await increment(
        db,
        llm_requests=usage.requests or 0,
        llm_input_tokens=usage.input_tokens or 0,
        llm_output_tokens=usage.output_tokens or 0,
    )

    # Finalize proposals: draft -> pending, superseding older open
    # revisions targeting the same (kind, entity). AUDIT BC-F2: the
    # supersede is a GUARDED UPDATE against current DB state —
    # `open_proposals` was loaded at turn START and the user may have
    # applied one mid-turn; overwriting `applied` with `superseded`
    # from a stale in-memory status would falsify the journal.
    for p in deps.emitted:
        p.status = ProposalStatus.pending
        for old in open_proposals:
            if (
                old.id != p.id
                and old.kind == p.kind
                and old.entity_type == p.entity_type
                and old.entity_id == p.entity_id
            ):
                res = await db.execute(
                    update(Proposal)
                    .where(
                        Proposal.id == old.id,
                        Proposal.status.in_(
                            (ProposalStatus.draft, ProposalStatus.pending)
                        ),
                    )
                    .values(status=ProposalStatus.superseded)
                )
                if res.rowcount:
                    p.supersedes_id = old.id
                    p.revision = old.revision + 1

    await db.commit()
    return RunOutcome(
        session_id=session.id,
        output=result.output,
        proposal_ids=[p.id for p in deps.emitted],
        message_range=(history_start, len(session.message_history or [])),
    )
