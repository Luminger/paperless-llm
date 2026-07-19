"""Harness-level proposal guards: no-op stripping/rejection and
referential integrity, enforced in the propose_* tools via ModelRetry.

"Nothing to propose" is expressed by not proposing; an emitted proposal
MUST change something real, or it bounces back to the model.
"""

from __future__ import annotations

import respx
from httpx import Response
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import select

from app.agents.deps import AgentDeps
from app.agents.runner import run_agent_turn
from app.agents.tools import ALL_TOOLS
from app.db.models import AgentKind, Proposal, Session
from tests.conftest import PAPERLESS_URL

DOC = {
    "id": 7,
    "title": "scan_0001",
    "content": "Telarko Rechnung 4711",
    "tags": [1, 5],
    "correspondent": None,
    "document_type": None,
    "storage_path": None,
    "created": "2024-04-17",
    "custom_fields": [],
}


def _page(items: list[dict]) -> dict:
    return {"count": len(items), "next": None, "previous": None, "results": items}


def _entity(id: int, name: str) -> dict:
    return {"id": id, "name": name, "match": "", "matching_algorithm": 1}


def _mock_paperless() -> None:
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(return_value=Response(200, json=DOC))
    respx.get(f"{PAPERLESS_URL}/api/tags/").mock(
        return_value=Response(200, json=_page([_entity(1, "Rechnung"), _entity(5, "scan")]))
    )
    respx.get(f"{PAPERLESS_URL}/api/correspondents/").mock(
        return_value=Response(200, json=_page([_entity(2, "Telarko")]))
    )
    respx.get(f"{PAPERLESS_URL}/api/document_types/").mock(
        return_value=Response(200, json=_page([_entity(1, "Rechnung")]))
    )
    respx.get(f"{PAPERLESS_URL}/api/storage_paths/").mock(
        return_value=Response(200, json=_page([]))
    )
    respx.get(f"{PAPERLESS_URL}/api/custom_fields/").mock(
        return_value=Response(200, json=_page([
            {"id": 3, "name": "Invoice number", "data_type": "string"},
        ]))
    )


def _scripted_agent(script: list[ModelResponse]) -> Agent:
    state = {"i": 0}

    async def fn(messages, info: AgentInfo) -> ModelResponse:
        step = script[min(state["i"], len(script) - 1)]
        state["i"] += 1
        return step

    return Agent(FunctionModel(fn), deps_type=AgentDeps, tools=list(ALL_TOOLS))


async def _make_session(db) -> Session:
    s = Session(agent_kind=AgentKind.document)
    db.add(s)
    await db.commit()
    return s


def _retry_texts(session: Session) -> str:
    out = []
    for m in session.message_history:
        for part in m.get("parts", []):
            if part.get("part_kind") == "retry-prompt":
                out.append(str(part.get("content")))
    return "\n".join(out)


async def _run(db, paperless_client, tool_call: ToolCallPart) -> Session:
    session = await _make_session(db)
    agent = _scripted_agent(
        [ModelResponse(parts=[tool_call]), ModelResponse(parts=[TextPart(content="ok")])]
    )
    await run_agent_turn(paperless_client, db, session, "go", agent=agent)
    return session


async def _proposals(db) -> list[Proposal]:
    return list((await db.scalars(select(Proposal))).all())


@respx.mock
async def test_full_noop_metadata_rejected(db, paperless_client):
    _mock_paperless()
    session = await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_update_document_metadata",
            # title identical, tag 1 already present, tag 9 not on doc.
            args={
                "document_id": 7,
                "title": "scan_0001",
                "add_tags": [1],
                "remove_tags": [9],
            },
        ),
    )
    assert await _proposals(db) == []
    assert "no-op" in _retry_texts(session)


@respx.mock
async def test_partial_noop_stripped(db, paperless_client):
    _mock_paperless()
    await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_update_document_metadata",
            args={
                "document_id": 7,
                "title": "scan_0001",  # no-op
                "document_type": 1,  # real change
                "created": "2024-04-17",  # no-op
            },
        ),
    )
    (p,) = await _proposals(db)
    assert p.agent_payload["document_type"] == 1
    assert "title" not in p.agent_payload
    assert "created" not in p.agent_payload


@respx.mock
async def test_dangling_correspondent_rejected(db, paperless_client):
    _mock_paperless()
    session = await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_update_document_metadata",
            args={"document_id": 7, "correspondent": 999},
        ),
    )
    assert await _proposals(db) == []
    assert "No correspondent with id=999" in _retry_texts(session)


@respx.mock
async def test_duplicate_entity_creation_rejected(db, paperless_client):
    _mock_paperless()
    session = await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_create_entity",
            args={"entity_type": "correspondent", "name": "  telarko "},
        ),
    )
    assert await _proposals(db) == []
    assert "already exists (id=2)" in _retry_texts(session)


@respx.mock
async def test_merge_with_self_rejected(db, paperless_client):
    _mock_paperless()
    session = await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_merge_entities",
            args={"entity_type": "tag", "source_id": 1, "target_id": 1},
        ),
    )
    assert await _proposals(db) == []
    assert "same entity" in _retry_texts(session)


@respx.mock
async def test_update_entity_noop_rejected(db, paperless_client):
    _mock_paperless()
    session = await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_update_entity",
            args={
                "entity_type": "tag",
                "entity_id": 1,
                "name": "Rechnung",  # identical
                "matching_algorithm": 1,  # identical
            },
        ),
    )
    assert await _proposals(db) == []
    assert "current state" in _retry_texts(session)


@respx.mock
async def test_one_proposal_per_turn(db, paperless_client):
    """The second propose call of a turn is rejected — the decision
    loop works one proposal at a time."""
    _mock_paperless()
    respx.get(f"{PAPERLESS_URL}/api/documents/1/").mock(
        return_value=Response(200, json=DOC | {"id": 1, "title": "Old"})
    )
    session = await _make_session(db)
    agent = _scripted_agent(
        [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="propose_update_document_metadata",
                        args={"document_id": 1, "title": "First"},
                        tool_call_id="c1",
                    )
                ]
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="propose_update_document_metadata",
                        args={"document_id": 1, "created": "2020-01-01"},
                        tool_call_id="c2",
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="done")]),
        ]
    )
    await run_agent_turn(paperless_client, db, session, "go", agent=agent)

    proposals = await _proposals(db)
    assert len(proposals) == 1  # only the first landed
    assert proposals[0].agent_payload.get("title") == "First"
    assert "One proposal per turn" in _retry_texts(session)


@respx.mock
async def test_pattern_algorithm_without_match_rejected(db, paperless_client):
    _mock_paperless()
    session = await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_create_entity",
            args={"entity_type": "correspondent", "name": "Telarko AG",
                  "matching_algorithm": 1},
        ),
    )
    assert await _proposals(db) == []
    assert "requires a `match` pattern" in _retry_texts(session)


@respx.mock
async def test_match_with_auto_algorithm_rejected(db, paperless_client):
    _mock_paperless()
    session = await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_create_entity",
            args={"entity_type": "correspondent", "name": "Telarko AG",
                  "match": "telarko", "matching_algorithm": 6},
        ),
    )
    assert await _proposals(db) == []
    assert "must not have one" in _retry_texts(session)


@respx.mock
async def test_rename_unaffected_by_inert_matching_state(db, paperless_client):
    """Entities often sit at paperless's inert default (algorithm=1,
    empty match); a plain rename must not trip the matching guard."""
    _mock_paperless()
    await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_update_entity",
            args={"entity_type": "correspondent", "entity_id": 2,
                  "name": "Telarko GmbH"},
        ),
    )
    (p,) = await _proposals(db)
    assert p.agent_payload["name"] == "Telarko GmbH"


@respx.mock
async def test_setting_word_match_rule(db, paperless_client):
    _mock_paperless()
    await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_update_entity",
            args={"entity_type": "correspondent", "entity_id": 2,
                  "match": "telarko", "matching_algorithm": 2},
        ),
    )
    (p,) = await _proposals(db)
    assert p.agent_payload["match"] == "telarko"
    assert p.agent_payload["matching_algorithm"] == 2


@respx.mock
async def test_create_entity_validates_assign_documents(db, paperless_client):
    """AUDIT BC-F5: assign_to_documents ids are validated at propose
    time — a nonexistent document bounces back to the model instead of
    reaching a privileged bulk write under auto policy."""
    _mock_paperless()
    respx.get(f"{PAPERLESS_URL}/api/documents/999/").mock(
        return_value=Response(404, json={"detail": "Not found."})
    )
    session = await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_create_entity",
            args={
                "entity_type": "correspondent",
                "name": "Kraxi GmbH",
                "assign_to_documents": [999],
            },
        ),
    )
    assert not await _proposals(db)
    assert "999" in _retry_texts(session)


@respx.mock
async def test_entity_payloads_carry_only_provided_fields(db, paperless_client):
    """AUDIT BC-F6: a rename-only update persists ONLY the name — no
    null match/matching_algorithm claiming the agent proposed clearing
    fields it never touched."""
    _mock_paperless()
    await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_update_entity",
            args={"entity_type": "correspondent", "entity_id": 2,
                  "name": "Telarko GmbH"},
        ),
    )
    (p,) = await _proposals(db)
    assert p.agent_payload["name"] == "Telarko GmbH"
    for absent in ("match", "matching_algorithm", "is_insensitive"):
        assert absent not in p.agent_payload

@respx.mock
async def test_custom_fields_validated_and_noops_dropped(db, paperless_client):
    """custom_fields values ride the same guard rails: unknown field ids
    are ModelRetries naming the registry; values equal to the document's
    current state are dropped as no-ops."""
    _mock_paperless()
    session = await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_update_document_metadata",
            args={"document_id": 7, "custom_fields": {"99": "x"}},
        ),
    )
    assert await _proposals(db) == []
    assert "Unknown custom field id 99" in _retry_texts(session)


@respx.mock
async def test_custom_fields_land_in_payload_and_snapshot(db, paperless_client):
    _mock_paperless()
    await _run(
        db,
        paperless_client,
        ToolCallPart(
            tool_name="propose_update_document_metadata",
            args={"document_id": 7, "custom_fields": {"3": "R-4711"}},
        ),
    )
    (p,) = await _proposals(db)
    assert p.agent_payload["custom_fields"] == {"3": "R-4711"}
    # snapshot records what the agent saw (field unset -> None)
    assert p.base_snapshot["custom_fields"] == {"3": None}
