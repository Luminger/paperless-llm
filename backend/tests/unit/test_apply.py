"""Apply engine against a respx-mocked paperless."""

from __future__ import annotations

import json

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
            "title": "Agent title",
        },
    )
    p.user_payload = {
        "kind": "update_document_metadata",
        "document_id": 7,
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
        {"kind": "delete_entity", "entity_type": "tag", "entity_id": 9},
    )
    with pytest.raises(ApplyError, match="referenced by 1 documents"):
        await apply_proposal(paperless_client, db, p)


async def test_apply_settled_status(db, paperless_client):
    p = await _make_proposal(
        db,
        {"kind": "replace_content", "document_id": 1, "content": "x"},
        status=ProposalStatus.superseded,
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
            "assign_to_documents": [7],
        },
    )
    change = await apply_proposal(paperless_client, db, p)

    assert change is not None and p.status == ProposalStatus.applied
    assert not create_route.called  # reused id=9
    assert bulk.called


@respx.mock
async def test_apply_create_entity_defaults_to_auto_matching(db, paperless_client):
    """Entities we create default to auto (ML) matching so paperless's
    classifier keeps learning; explicit rules pass through untouched."""
    respx.get(f"{PAPERLESS_URL}/api/correspondents/").mock(
        return_value=Response(200, json={"count": 0, "next": None, "results": []})
    )
    create_route = respx.post(f"{PAPERLESS_URL}/api/correspondents/").mock(
        return_value=Response(201, json={"id": 9, "name": "Neu", "document_count": 0,
                                          "match": "", "matching_algorithm": 6})
    )

    p = await _make_proposal(
        db, {"kind": "create_entity", "entity_type": "correspondent", "name": "Neu"}
    )
    await apply_proposal(paperless_client, db, p)
    body = json.loads(create_route.calls.last.request.content)
    assert body["matching_algorithm"] == 6
    assert "match" not in body

    p2 = await _make_proposal(
        db, {"kind": "create_entity", "entity_type": "correspondent",
             "name": "Neu Zwei", "match": "neu zwei", "matching_algorithm": 2}
    )
    respx.post(f"{PAPERLESS_URL}/api/correspondents/").mock(
        return_value=Response(201, json={"id": 10, "name": "Neu Zwei",
                                          "match": "neu zwei", "matching_algorithm": 2})
    )
    await apply_proposal(paperless_client, db, p2)
    body = json.loads(respx.calls.last.request.content)
    assert body["matching_algorithm"] == 2
    assert body["match"] == "neu zwei"


@respx.mock
async def test_apply_conflicts_when_paperless_moved(db, paperless_client):
    """Optimistic concurrency: the snapshot of what the agent looked at
    no longer matches paperless -> 409-style ApplyError, nothing written."""
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Someone renamed me"})
    )
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/")

    p = await _make_proposal(
        db,
        {
            "kind": "update_document_metadata",
            "document_id": 7,
            "title": "Agent title",
        },
    )
    p.base_snapshot = {"title": "scan_0001"}  # what the agent saw
    await db.commit()

    with pytest.raises(ApplyError, match="changed since this was proposed"):
        await apply_proposal(paperless_client, db, p)
    assert not patch_route.called
    assert p.status == ProposalStatus.pending  # still reviewable


@respx.mock
async def test_apply_no_conflict_when_field_converged(db, paperless_client):
    """A field that moved TO the proposed value doesn't conflict; other
    proposed fields still apply."""
    doc = DOC | {"title": "Agent title"}  # title already converged
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(return_value=Response(200, json=doc))
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=doc | {"tags": [1, 5, 2]})
    )
    p = await _make_proposal(
        db,
        {
            "kind": "update_document_metadata",
            "document_id": 7,
            "title": "Agent title",
            "add_tags": [2],
        },
    )
    p.base_snapshot = {"title": "scan_0001", "tags": [1, 5]}
    await db.commit()

    change = await apply_proposal(paperless_client, db, p)
    assert change is not None and patch_route.called


@respx.mock
async def test_delete_conflicts_when_documents_appeared(db, paperless_client):
    respx.get(f"{PAPERLESS_URL}/api/tags/9/").mock(
        return_value=Response(200, json={
            "id": 9, "name": "Junk", "document_count": 4,
            "match": "", "matching_algorithm": 0,
        })
    )
    p = await _make_proposal(
        db,
        {"kind": "delete_entity", "entity_type": "tag", "entity_id": 9},
    )
    p.base_snapshot = {"name": "Junk", "document_count": 0}
    await db.commit()

    with pytest.raises(ApplyError, match="document count was 0, now 4"):
        await apply_proposal(paperless_client, db, p)


@respx.mock
async def test_revert_noop_detection_and_guard(db, paperless_client):
    """When paperless already matches the pre-apply snapshot, the revert
    is a noop: detected by revert_is_noop and refused by revert_change."""
    from app.db.models import AppliedChange
    from app.proposals.apply import revert_change, revert_is_noop

    p = await _make_proposal(
        db,
        {
            "kind": "update_document_metadata",
            "document_id": 7,
            "title": "Agent title",
        },
        status=ProposalStatus.applied,
    )
    change = AppliedChange(
        proposal_id=p.id,
        paperless_before={"document": {"id": 7, "title": "scan_0001"}},
        paperless_after={"document": {"id": 7, "title": "Agent title"}},
    )
    db.add(change)
    await db.commit()
    await db.refresh(change, ["proposal"])

    # Someone already renamed it back -> noop.
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "scan_0001"})
    )
    assert await revert_is_noop(paperless_client, p, change) is True
    with pytest.raises(ApplyError, match="nothing to undo"):
        await revert_change(paperless_client, db, change)
    assert change.reverted_at is None  # untouched

    # Still holding the applied title -> a real revert.
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Agent title"})
    )
    assert await revert_is_noop(paperless_client, p, change) is False


@respx.mock
async def test_revert_noop_for_deleted_created_entity(db, paperless_client):
    from app.db.models import AppliedChange
    from app.proposals.apply import revert_is_noop

    p = await _make_proposal(
        db,
        {"kind": "create_entity", "entity_type": "tag", "name": "Neu"},
        status=ProposalStatus.applied,
    )
    change = AppliedChange(
        proposal_id=p.id,
        paperless_before={"entity": None, "entity_type": "tag"},
        paperless_after={"entity": {"id": 55, "name": "Neu"}, "assigned_documents": []},
    )
    db.add(change)
    await db.commit()
    await db.refresh(change, ["proposal"])

    # Entity already deleted again -> reverting (deleting) is a noop.
    respx.get(f"{PAPERLESS_URL}/api/tags/55/").mock(return_value=Response(404, json={}))
    assert await revert_is_noop(paperless_client, p, change) is True
    # Entity still there -> real revert.
    respx.get(f"{PAPERLESS_URL}/api/tags/55/").mock(
        return_value=Response(200, json={"id": 55, "name": "Neu", "document_count": 0,
                                          "match": "", "matching_algorithm": 0})
    )
    assert await revert_is_noop(paperless_client, p, change) is False


# ----- AUDIT API-F1 / API-F5 / API-F6 regression tests -----------------


@respx.mock
async def test_revert_of_reused_entity_never_deletes_it(db, paperless_client):
    """AUDIT API-F1: the apply REUSED a pre-existing entity — revert must
    only undo our document assignments, never delete the entity."""
    from app.proposals.apply import revert_change

    respx.get(f"{PAPERLESS_URL}/api/correspondents/").mock(
        return_value=Response(200, json={"count": 1, "next": None, "results": [
            {"id": 9, "name": "Kraxi", "document_count": 41, "match": "",
             "matching_algorithm": 0}
        ]})
    )
    respx.get(f"{PAPERLESS_URL}/api/correspondents/9/").mock(
        return_value=Response(200, json={
            "id": 9, "name": "Kraxi", "document_count": 41, "match": "",
            "matching_algorithm": 0,
        })
    )
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC)
    )
    bulk = respx.post(f"{PAPERLESS_URL}/api/documents/bulk_edit/").mock(
        return_value=Response(200, json={"result": "OK"})
    )
    delete_route = respx.delete(f"{PAPERLESS_URL}/api/correspondents/9/")

    p = await _make_proposal(
        db,
        {
            "kind": "create_entity",
            "entity_type": "correspondent",
            "name": "Kraxi",
            "assign_to_documents": [7],
        },
    )
    change = await apply_proposal(paperless_client, db, p)
    assert change is not None
    assert change.paperless_after["reused"] is True
    assert change.paperless_before["entity"]["id"] == 9  # honest snapshot

    await revert_change(paperless_client, db, change)
    assert not delete_route.called  # the 41-document entity survives
    # Our assignment was undone (correspondent cleared on doc 7).
    body = json.loads(bulk.calls[-1].request.content)
    assert body["parameters"] == {"correspondent": None}
    assert change.reverted_at is not None


@respx.mock
async def test_title_only_revert_does_not_touch_tags(db, paperless_client):
    """AUDIT API-F6: the snapshot carries only proposed fields — a
    title-only revert must not clobber tag edits made since."""
    from app.proposals.apply import revert_change

    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC)
    )
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Better title"})
    )
    p = await _make_proposal(
        db,
        {"kind": "update_document_metadata", "document_id": 7, "title": "Better title"},
    )
    change = await apply_proposal(paperless_client, db, p)
    assert "tags" not in change.paperless_before["document"]

    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(
            200, json=DOC | {"title": "Better title", "tags": [1, 5, 99]}
        )
    )
    await revert_change(paperless_client, db, change)
    body = json.loads(patch_route.calls[-1].request.content)
    assert body == {"title": "scan_0001"}  # no tags key at all


@respx.mock
async def test_tag_revert_is_a_delta(db, paperless_client):
    """AUDIT API-F6: reverting a tag change re-adds/removes ONLY what the
    apply changed — a tag added in paperless since survives."""
    from app.proposals.apply import revert_change

    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC)  # tags [1, 5]
    )
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"tags": [1, 2]})
    )
    p = await _make_proposal(
        db,
        {
            "kind": "update_document_metadata",
            "document_id": 7,
            "add_tags": [2],
            "remove_tags": [5],
        },
    )
    change = await apply_proposal(paperless_client, db, p)  # [1,5] -> [1,2]

    # Since the apply, someone added tag 99 in paperless.
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"tags": [1, 2, 99]})
    )
    await revert_change(paperless_client, db, change)
    body = json.loads(patch_route.calls[-1].request.content)
    # 2 (added by us) removed, 5 (removed by us) restored, 99 SURVIVES.
    assert sorted(body["tags"]) == [1, 5, 99]


@respx.mock
async def test_second_revert_conflicts(db, paperless_client):
    """AUDIT API-F5: the revert claim is atomic — a second revert of the
    same change raises instead of double-executing paperless writes."""
    from app.proposals.apply import revert_change

    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC)
    )
    respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Better title"})
    )
    p = await _make_proposal(
        db,
        {"kind": "update_document_metadata", "document_id": 7, "title": "Better title"},
    )
    change = await apply_proposal(paperless_client, db, p)

    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Better title"})
    )
    await revert_change(paperless_client, db, change)
    with pytest.raises(ApplyError, match="already reverted"):
        await revert_change(paperless_client, db, change)
