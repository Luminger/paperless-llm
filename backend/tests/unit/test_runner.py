"""Agent runner with scripted models (no LLM, no paperless network
except respx where tools need it).

Note: propose_* tools validate against live paperless state (guards),
so every test whose script emits a proposal mocks the document +
taxonomy endpoints."""

from __future__ import annotations

import respx
from httpx import Response
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agents.deps import AgentDeps
from app.agents.runner import run_agent_turn
from app.agents.tools import ALL_TOOLS
from app.db.models import AgentKind, ProposalStatus, Session, SessionStatus
from tests.conftest import PAPERLESS_URL

DOC7 = {
    "id": 7,
    "title": "scan_0001",
    "content": "Telarko Rechnung ...",
    "tags": [],
    "correspondent": None,
    "document_type": None,
    "storage_path": None,
    "created": "2024-04-17",
    "custom_fields": [],
}


def _mock_doc7() -> None:
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC7)
    )


def _scripted_agent(script):
    """FunctionModel emitting a fixed sequence of responses."""
    state = {"i": 0}

    async def fn(messages, info: AgentInfo) -> ModelResponse:
        step = script[min(state["i"], len(script) - 1)]
        state["i"] += 1
        return step

    return Agent(FunctionModel(fn), deps_type=AgentDeps, tools=list(ALL_TOOLS))


def _propose_title_script(title: str):
    return [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="propose_update_document_metadata",
                    args={"document_id": 7, "title": title},
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Proposed a title fix.")]),
    ]


async def _make_session(db) -> Session:
    s = Session(agent_kind=AgentKind.document)
    db.add(s)
    await db.commit()
    return s


@respx.mock
async def test_turn_persists_history_and_proposal(db, paperless_client):
    _mock_doc7()
    session = await _make_session(db)
    agent = _scripted_agent(_propose_title_script("Telarko Rechnung April 2024"))

    outcome = await run_agent_turn(
        paperless_client, db, session, "Process document id=7.", agent=agent
    )

    assert outcome.output == "Proposed a title fix."
    assert len(outcome.proposal_ids) == 1
    assert session.status == SessionStatus.idle
    # History: user prompt + tool call + tool return + final text.
    assert len(session.message_history) >= 3

    from sqlalchemy import select

    from app.db.models import Proposal

    p = await db.scalar(select(Proposal))
    assert p.status == ProposalStatus.pending
    assert p.agent_payload["title"] == "Telarko Rechnung April 2024"
    assert p.entity_id == 7
    assert p.user_payload is None


@respx.mock
async def test_second_turn_supersedes(db, paperless_client):
    _mock_doc7()
    session = await _make_session(db)
    await run_agent_turn(
        paperless_client, db, session, "Process document id=7.",
        agent=_scripted_agent(_propose_title_script("First title")),
    )
    outcome2 = await run_agent_turn(
        paperless_client, db, session, "Please use the German date format in the title.",
        agent=_scripted_agent(_propose_title_script("Zweiter Titel")),
    )

    from sqlalchemy import select

    from app.db.models import Proposal

    proposals = (await db.scalars(select(Proposal).order_by(Proposal.id))).all()
    assert len(proposals) == 2
    first, second = proposals
    assert first.status == ProposalStatus.superseded
    assert second.status == ProposalStatus.pending
    assert second.supersedes_id == first.id
    assert second.revision == 2
    assert outcome2.proposal_ids == [second.id]


@respx.mock
async def test_user_edit_injected_into_steering(db, paperless_client):
    _mock_doc7()
    session = await _make_session(db)
    await run_agent_turn(
        paperless_client, db, session, "Process document id=7.",
        agent=_scripted_agent(_propose_title_script("Agent title")),
    )
    from sqlalchemy import select

    from app.db.models import Proposal

    p = await db.scalar(select(Proposal))
    p.user_payload = p.agent_payload | {"title": "User fixed title"}
    await db.commit()

    seen: list[str] = []

    async def fn(messages, info: AgentInfo) -> ModelResponse:
        # The preamble travels as run-time instructions (NOT in the user
        # prompt — that keeps the derived transcript clean).
        for m in messages:
            if isinstance(getattr(m, "instructions", None), str):
                seen.append(m.instructions)
            for part in getattr(m, "parts", []):
                content = getattr(part, "content", None)
                if isinstance(content, str):
                    seen.append(content)
        return ModelResponse(parts=[TextPart(content="ok")])

    agent = Agent(FunctionModel(fn), deps_type=AgentDeps, tools=list(ALL_TOOLS))
    await run_agent_turn(paperless_client, db, session, "Looks good?", agent=agent)

    joined = "\n".join(seen)
    assert "User fixed title" in joined
    assert "manually amended" in joined
    # The user-visible chat message must stay unpolluted.
    assert any(s == "Looks good?" for s in seen)


@respx.mock
async def test_failure_keeps_drafts_reviewable(db, paperless_client):
    _mock_doc7()
    session = await _make_session(db)

    calls = {"n": 0}

    async def fn(messages, info: AgentInfo) -> ModelResponse:
        if calls["n"] == 0:
            calls["n"] += 1
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="propose_update_document_metadata",
                        args={"document_id": 7, "title": "T"},
                    )
                ]
            )
        raise RuntimeError("endpoint exploded")

    agent = Agent(FunctionModel(fn), deps_type=AgentDeps, tools=list(ALL_TOOLS))

    import pytest

    with pytest.raises(RuntimeError):
        await run_agent_turn(paperless_client, db, session, "go", agent=agent)
    # Failure bookkeeping (status/error/retries) is the step engine's
    # job now; the runner only guarantees drafts stay reviewable.

    from sqlalchemy import select

    from app.db.models import Proposal

    p = await db.scalar(select(Proposal))
    assert p is not None and p.status == ProposalStatus.pending  # still reviewable


@respx.mock
async def test_read_tool_roundtrip(db, paperless_client):
    """A scripted run that actually exercises a read tool via respx."""
    _mock_doc7()
    session = await _make_session(db)
    script = [
        ModelResponse(parts=[ToolCallPart(tool_name="get_document", args={"document_id": 7})]),
        ModelResponse(parts=[TextPart(content="done")]),
    ]
    outcome = await run_agent_turn(
        paperless_client, db, session, "look at doc 7", agent=_scripted_agent(script)
    )
    assert outcome.output == "done"


@respx.mock
async def test_instrumented_tools_publish_step_progress(db, paperless_client):
    """The registry wraps tools to publish tool_called events; the wrap
    must survive pydantic-ai's signature-based schema generation, and a
    full turn must emit run_started/tool_called/proposal_created/
    run_finished on the bus."""
    from app.agents.registry import _instrumented
    from app.services.events import bus

    _mock_doc7()
    session = await _make_session(db)
    q = bus.subscribe(session.id)
    try:
        state = {"i": 0}
        script = _propose_title_script("Telarko Rechnung April 2024")

        async def fn(messages, info: AgentInfo) -> ModelResponse:
            step = script[min(state["i"], len(script) - 1)]
            state["i"] += 1
            return step

        agent = Agent(
            FunctionModel(fn),
            deps_type=AgentDeps,
            tools=[_instrumented(t) for t in ALL_TOOLS],
        )
        outcome = await run_agent_turn(
            paperless_client, db, session, "Process document id=7.", agent=agent
        )
        assert outcome.proposal_ids  # tool executed through the wrapper

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        tools = [e for e in events if e["type"] == "step_progress" and e.get("tool")]
        assert tools and tools[0]["tool"] == "propose_update_document_metadata"
    finally:
        bus.unsubscribe(session.id, q)


@respx.mock
async def test_taxonomy_agent_merge_flow(db, paperless_client):
    """A scripted taxonomy turn: the merge proposal validates against
    live taxonomy and persists with the right target entity."""
    from app.agents.tools import TAXONOMY_AGENT_TOOLS

    respx.get(f"{PAPERLESS_URL}/api/correspondents/").mock(
        return_value=Response(
            200,
            json={"count": 2, "next": None, "results": [
                {"id": 4, "name": "Kraxi GmbH", "document_count": 1,
                 "match": "", "matching_algorithm": 0},
                {"id": 8, "name": "Kraxi", "document_count": 5,
                 "match": "", "matching_algorithm": 0},
            ]},
        )
    )
    session = Session(agent_kind=AgentKind.correspondent)
    db.add(session)
    await db.commit()

    script = [
        ModelResponse(parts=[
            ToolCallPart(
                tool_name="propose_merge_entities",
                args={"entity_type": "correspondent", "source_id": 4,
                      "target_id": 8},
            )
        ]),
        ModelResponse(parts=[TextPart(content="Proposed merging the duplicate.")]),
    ]
    state = {"i": 0}

    async def fn(messages, info: AgentInfo) -> ModelResponse:
        step = script[min(state["i"], len(script) - 1)]
        state["i"] += 1
        return step

    agent = Agent(FunctionModel(fn), deps_type=AgentDeps, tools=list(TAXONOMY_AGENT_TOOLS))
    outcome = await run_agent_turn(
        paperless_client, db, session, "Review correspondent id=4.", agent=agent
    )
    assert len(outcome.proposal_ids) == 1

    from sqlalchemy import select

    from app.db.models import Proposal

    p = await db.scalar(select(Proposal))
    assert p.kind == "merge_entities"
    assert p.agent_payload["source_id"] == 4 and p.agent_payload["target_id"] == 8
    assert p.entity_type.value == "correspondent"
    # Snapshot of what the agent looked at (drives review UI + staleness check).
    assert p.base_snapshot["source"]["name"] == "Kraxi GmbH"
    assert p.base_snapshot["target"]["name"] == "Kraxi"


def test_all_agent_kinds_buildable(monkeypatch):
    """Prompts + instrumented toolsets survive agent construction for
    every kind (schema generation happens here)."""
    from app.agents.registry import build_agent
    from app.db.models import AgentKind

    for kind in AgentKind:
        assert build_agent(kind) is not None
