"""Apply engine against a respx-mocked paperless."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.db.models import AgentKind, Proposal, ProposalStatus, Session
from app.proposals import ApplyError, apply_proposal
from tests.conftest import PAPERLESS_URL

DOC = {
    "id": 7,
    "title": "scan_0001",
    "content": "old content",
    "tags": [1, 5],
    "correspondent": None,
    "document_type": None,
    "storage_path": None,
    "created": "2024-04-17",
    "custom_fields": [],
}


async def _make_proposal(db, payload: dict, status=ProposalStatus.pending) -> Proposal:
    session = Session(agent_kind=AgentKind.document)
    db.add(session)
    await db.flush()
    p = Proposal(
        session_id=session.id, kind=payload["kind"], agent_payload=payload, status=status
    )
    db.add(p)
    await db.commit()
    return p


@respx.mock
async def test_apply_document_metadata_merges_tags(db, paperless_client):
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC)
    )
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(
            200, json=DOC | {"title": "Telarko Rechnung April 2024", "tags": [1, 2]}
        )
    )
    p = await _make_proposal(
        db,
        {
            "kind": "update_document_metadata",
            "document_id": 7,
            "reason": "better title",
            "title": "Telarko Rechnung April 2024",
            "add_tags": [2],
            "remove_tags": [5],
        },
    )
    change = await apply_proposal(paperless_client, db, p)

    sent = patch_route.calls.last.request
    import json

    body = json.loads(sent.content)
    assert body["title"] == "Telarko Rechnung April 2024"
    assert body["tags"] == [1, 2]  # 5 removed, 2 added, 1 kept
    assert p.status == ProposalStatus.applied
    assert change.paperless_before["document"]["tags"] == [1, 5]


@respx.mock
async def test_user_payload_wins(db, paperless_client):
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC)
    )
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "User title"})
    )
    p = await _make_proposal(
        db,
        {
            "kind": "update_document_metadata",
            "document_id": 7,
            "reason": "r",
            "title": "Agent title",
        },
    )
    p.user_payload = {
        "kind": "update_document_metadata",
        "document_id": 7,
        "reason": "r",
        "title": "User title",
    }
    await db.commit()
    await apply_proposal(paperless_client, db, p)

    import json

    assert json.loads(patch_route.calls.last.request.content)["title"] == "User title"


@respx.mock
async def test_apply_merge_correspondents(db, paperless_client):
    respx.get(f"{PAPERLESS_URL}/api/correspondents/2/").mock(
        return_value=Response(
            200, json={"id": 2, "name": "Telarko Deutschland GmbH", "match": "",
                       "matching_algorithm": 0}
        )
    )
    respx.get(f"{PAPERLESS_URL}/api/correspondents/1/").mock(
        return_value=Response(
            200, json={"id": 1, "name": "Telarko", "match": "",
                       "matching_algorithm": 0}
        )
    )
    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(
        return_value=Response(
            200,
            json={
                "count": 2,
                "next": None,
                "previous": None,
                "all": [11, 12],
                "results": [DOC | {"id": 11}, DOC | {"id": 12}],
            },
        )
    )
    bulk_route = respx.post(f"{PAPERLESS_URL}/api/documents/bulk_edit/").mock(
        return_value=Response(200, json={"result": "OK"})
    )
    delete_route = respx.delete(f"{PAPERLESS_URL}/api/correspondents/2/").mock(
        return_value=Response(204)
    )

    p = await _make_proposal(
        db,
        {
            "kind": "merge_entities",
            "entity_type": "correspondent",
            "source_id": 2,
            "target_id": 1,
            "reason": "duplicate",
        },
    )
    change = await apply_proposal(paperless_client, db, p)

    import json

    body = json.loads(bulk_route.calls.last.request.content)
    assert body == {
        "documents": [11, 12],
        "method": "set_correspondent",
        "parameters": {"correspondent": 1},
    }
    assert delete_route.called
    assert change.paperless_before["documents_reassigned"] == [11, 12]
    assert change.paperless_before["source_entity"]["name"] == "Telarko Deutschland GmbH"


@respx.mock
async def test_delete_entity_refused_when_referenced(db, paperless_client):
    respx.get(f"{PAPERLESS_URL}/api/tags/9/").mock(
        return_value=Response(
            200, json={"id": 9, "name": "old-stuff-2019", "match": "", "matching_algorithm": 0}
        )
    )
    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(
        return_value=Response(
            200,
            json={"count": 1, "next": None, "previous": None, "all": [3],
                  "results": [DOC | {"id": 3}]},
        )
    )
    p = await _make_proposal(
        db,
        {"kind": "delete_entity", "entity_type": "tag", "entity_id": 9, "reason": "junk"},
    )
    with pytest.raises(ApplyError, match="referenced by 1 documents"):
        await apply_proposal(paperless_client, db, p)


async def test_apply_rejected_status(db, paperless_client):
    p = await _make_proposal(
        db,
        {"kind": "replace_content", "document_id": 1, "content": "x", "reason": "r"},
        status=ProposalStatus.rejected,
    )
    with pytest.raises(ApplyError, match="cannot apply"):
        await apply_proposal(paperless_client, db, p)


@respx.mock
async def test_apply_detects_state_already_matching(db, paperless_client):
    """State moved between emit and apply (concurrent session, retry):
    the proposal becomes no_change — nothing written, nothing journaled."""
    doc = DOC | {"title": "Agent title", "tags": [1, 5]}
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(return_value=Response(200, json=doc))
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/")

    p = await _make_proposal(
        db,
        {
            "kind": "update_document_metadata",
            "document_id": 7,
            "reason": "r",
            "title": "Agent title",  # already the current title
        },
    )
    change = await apply_proposal(paperless_client, db, p)

    assert change is None
    assert p.status == ProposalStatus.no_change
    assert not patch_route.called
    from sqlalchemy import select

    from app.db.models import AppliedChange

    assert (await db.scalar(select(AppliedChange))) is None


@respx.mock
async def test_apply_create_entity_reuses_existing_name(db, paperless_client):
    """An identically-named entity appeared since the proposal: reuse it
    for assignment instead of erroring on a duplicate create."""
    respx.get(f"{PAPERLESS_URL}/api/correspondents/").mock(
        return_value=Response(200, json={"count": 1, "next": None, "results": [
            {"id": 9, "name": "Kraxi", "document_count": 0, "match": "",
             "matching_algorithm": 0}
        ]})
    )
    create_route = respx.post(f"{PAPERLESS_URL}/api/correspondents/")
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC)  # correspondent: None -> assign needed
    )
    bulk = respx.post(f"{PAPERLESS_URL}/api/documents/bulk_edit/").mock(
        return_value=Response(200, json={"result": "OK"})
    )

    p = await _make_proposal(
        db,
        {
            "kind": "create_entity",
            "entity_type": "correspondent",
            "name": "Kraxi",
            "reason": "r",
            "assign_to_documents": [7],
        },
    )
    change = await apply_proposal(paperless_client, db, p)

    assert change is not None and p.status == ProposalStatus.applied
    assert not create_route.called  # reused id=9
    assert bulk.called
