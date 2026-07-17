"""Runs one agent turn against a persisted Session: loads history,
executes with the endpoint semaphore and iteration cap, persists new
history, and finalizes emitted proposals (draft -> pending, superseding
older open revisions for the same target).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.usage import UsageLimits
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.deps import AgentDeps
from app.agents.registry import build_agent
from app.config import get_settings
from app.db.models import Proposal, ProposalStatus, Session, SessionStatus
from app.llm.factory import llm_semaphore
from app.paperless import PaperlessClient
from app.services.events import bus


@dataclass
class RunOutcome:
    session_id: int
    output: str
    proposal_ids: list[int] = field(default_factory=list)


def _load_history(session: Session) -> list[ModelMessage]:
    if not session.message_history:
        return []
    return ModelMessagesTypeAdapter.validate_python(session.message_history)


def _dump_history(messages: list[ModelMessage]) -> list:
    return ModelMessagesTypeAdapter.dump_python(messages, mode="json")


def _progress_handler(session_id: int):
    """Event-stream consumer for streaming runs: publishes a throttled
    'generating' SSE signal so the UI can show live progress instead of
    an opaque spinner. Streamed chunks arrive roughly one token each,
    so the delta-event count is an honest token proxy; visible (non-
    thinking) text is forwarded as a tail preview. Passing a handler is
    also what switches pydantic-ai to streamed model requests — only
    used when the profile declares supports_streaming."""
    from pydantic_ai.messages import TextPartDelta

    async def handler(ctx, events) -> None:
        tokens = 0
        text = ""
        last_publish = 0.0
        async for ev in events:
            delta = getattr(ev, "delta", None)
            if delta is not None:
                tokens += 1
                if isinstance(delta, TextPartDelta):
                    text += delta.content_delta
            now = time.monotonic()
            if tokens and now - last_publish >= 1.0:
                bus.publish(
                    session_id,
                    "generating",
                    tokens=tokens,
                    text_tail=text[-400:],
                )
                last_publish = now

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
) -> RunOutcome:
    """Execute one turn. ``agent`` is injectable for tests (TestModel /
    FunctionModel); defaults to the configured kind."""
    settings = get_settings()
    profile = settings.llm.agent
    agent = agent or build_agent(session.agent_kind)

    deps = AgentDeps(
        paperless=paperless, db=db, settings=settings, session_id=session.id
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

    session.status = SessionStatus.running
    session.error = None
    await db.commit()
    bus.publish(session.id, "run_started")

    try:
        async with llm_semaphore(profile.base_url, profile.max_concurrent):
            result = await agent.run(
                user_message,
                deps=deps,
                instructions=preamble,
                message_history=_load_history(session),
                usage_limits=UsageLimits(request_limit=profile.max_tool_iterations),
                event_stream_handler=(
                    _progress_handler(session.id) if profile.supports_streaming else None
                ),
            )
    except Exception as e:
        session.status = SessionStatus.failed
        session.error = f"{type(e).__name__}: {e}"
        # Keep any proposals drafted before the failure reviewable.
        for p in deps.emitted:
            p.status = ProposalStatus.pending
        await db.commit()
        raise

    session.message_history = _dump_history(result.all_messages())
    session.status = SessionStatus.idle
    if not session.title:
        session.title = user_message[:200]

    # Finalize proposals: draft -> pending, superseding older open
    # revisions targeting the same (kind, entity).
    for p in deps.emitted:
        p.status = ProposalStatus.pending
        for old in open_proposals:
            if (
                old.id != p.id
                and old.kind == p.kind
                and old.entity_type == p.entity_type
                and old.entity_id == p.entity_id
                and old.status in (ProposalStatus.draft, ProposalStatus.pending)
            ):
                old.status = ProposalStatus.superseded
                p.supersedes_id = old.id
                p.revision = old.revision + 1

    await db.commit()
    for p in deps.emitted:
        bus.publish(session.id, "proposal_created", proposal_id=p.id, kind=p.kind)
    bus.publish(session.id, "run_finished")
    return RunOutcome(
        session_id=session.id,
        output=result.output,
        proposal_ids=[p.id for p in deps.emitted],
    )
