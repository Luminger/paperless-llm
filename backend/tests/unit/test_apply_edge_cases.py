"""Apply-engine edge cases: no_change verdicts per proposal kind,
base_snapshot conflicts on entity proposals, claim release on failure,
and the revert paths that RECREATE deleted/merged-away entities.

These paths guard against silent data loss: a revert that forgets the
reassigned documents, or a failed apply that leaves the proposal stuck
in `applying`, would strand user data with no UI recourse."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response
from sqlalchemy import select

from app.db.models import AgentKind, AppliedChange, Proposal, ProposalStatus, Session
from app.paperless import PaperlessError
from app.proposals import ApplyError, apply_proposal, revert_change
from app.proposals.apply import revert_is_noop
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


async def _make_change(
    db, payload: dict, before: dict, after: dict
) -> tuple[Proposal, AppliedChange]:
    """Returns (proposal, change). revert_change loads change.proposal
    itself (claim refresh includes the relationship), so callers need no
    identity-map tricks — see test_revert_with_only_the_change_loaded."""
    p = await _make_proposal(db, payload, status=ProposalStatus.applied)
    change = AppliedChange(
        proposal_id=p.id, paperless_before=before, paperless_after=after
    )
    db.add(change)
    await db.commit()
    return p, change


def _tag(tag_id: int, name: str, **extra) -> dict:
    return {"id": tag_id, "name": name, "match": "", "matching_algorithm": 0,
            "is_insensitive": True, "document_count": 0} | extra


# ----- no_change verdicts ---------------------------------------------


@respx.mock
async def test_replace_content_noop_ignores_surrounding_whitespace(db, paperless_client):
    """OCR text that differs only in leading/trailing whitespace is the
    same content: writing it would create journal noise for nothing."""
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"content": "old content\n\n"})
    )
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/")
    p = await _make_proposal(
        db, {"kind": "replace_content", "document_id": 7, "content": "old content"}
    )
    assert await apply_proposal(paperless_client, db, p) is None
    assert p.status == ProposalStatus.no_change
    assert not patch_route.called


@respx.mock
async def test_update_entity_noop_when_already_matching(db, paperless_client):
    """Entity rename that another session already performed: no write,
    no journal entry, verdict no_change."""
    respx.get(f"{PAPERLESS_URL}/api/tags/9/").mock(
        return_value=Response(200, json=_tag(9, "steuern-2024"))
    )
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/tags/9/")
    p = await _make_proposal(
        db, {"kind": "update_entity", "entity_type": "tag", "entity_id": 9,
             "name": "steuern-2024"}
    )
    assert await apply_proposal(paperless_client, db, p) is None
    assert p.status == ProposalStatus.no_change
    assert not patch_route.called


@respx.mock
async def test_merge_noop_when_source_already_gone(db, paperless_client):
    """The merge source was already merged away/deleted (e.g. the same
    duplicate spotted by two sessions): applying again is a clean
    no_change, not a 404 error."""
    respx.get(f"{PAPERLESS_URL}/api/correspondents/2/").mock(
        return_value=Response(404, json={"detail": "not found"})
    )
    p = await _make_proposal(
        db, {"kind": "merge_entities", "entity_type": "correspondent",
             "source_id": 2, "target_id": 1}
    )
    assert await apply_proposal(paperless_client, db, p) is None
    assert p.status == ProposalStatus.no_change


# ----- base_snapshot conflicts ----------------------------------------


@respx.mock
async def test_update_entity_conflict_when_entity_moved(db, paperless_client):
    """The entity was renamed since the agent looked at it — applying the
    stale proposal must stop for review, not overwrite the newer name."""
    respx.get(f"{PAPERLESS_URL}/api/tags/9/").mock(
        return_value=Response(200, json=_tag(9, "renamed-by-human"))
    )
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/tags/9/")
    p = await _make_proposal(
        db, {"kind": "update_entity", "entity_type": "tag", "entity_id": 9,
             "name": "agent-name"}
    )
    p.base_snapshot = {"name": "original-name"}
    await db.commit()
    with pytest.raises(ApplyError, match="changed since this was proposed"):
        await apply_proposal(paperless_client, db, p)
    assert not patch_route.called
    assert p.status == ProposalStatus.pending  # still reviewable


@respx.mock
async def test_merge_conflict_when_target_vanished(db, paperless_client):
    """Merging INTO a deleted target would strand every reassigned
    document — the apply must refuse with a conflict."""
    respx.get(f"{PAPERLESS_URL}/api/correspondents/2/").mock(
        return_value=Response(200, json=_tag(2, "Telarko GmbH"))
    )
    respx.get(f"{PAPERLESS_URL}/api/correspondents/1/").mock(
        return_value=Response(404, json={"detail": "not found"})
    )
    p = await _make_proposal(
        db, {"kind": "merge_entities", "entity_type": "correspondent",
             "source_id": 2, "target_id": 1}
    )
    p.base_snapshot = {"source": {"name": "Telarko GmbH"}, "target": {"name": "Telarko"}}
    await db.commit()
    with pytest.raises(ApplyError, match="merge target #1 no longer exists"):
        await apply_proposal(paperless_client, db, p)
    assert p.status == ProposalStatus.pending


# ----- claim handling --------------------------------------------------


@respx.mock
async def test_merge_source_equals_target_rejected_and_claim_released(db, paperless_client):
    respx.get(f"{PAPERLESS_URL}/api/tags/9/").mock(
        return_value=Response(200, json=_tag(9, "dup"))
    )
    p = await _make_proposal(
        db, {"kind": "merge_entities", "entity_type": "tag",
             "source_id": 9, "target_id": 9}
    )
    with pytest.raises(ApplyError, match="identical"):
        await apply_proposal(paperless_client, db, p)
    assert p.status == ProposalStatus.pending


@respx.mock
async def test_failed_apply_releases_claim_for_retry(db, paperless_client):
    """A paperless write failure must flip the proposal back to pending
    (nothing was journaled) so the user can simply retry — a proposal
    stuck in `applying` would be dead in the UI."""
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC)
    )
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(500, text="boom")
    )
    p = await _make_proposal(
        db, {"kind": "update_document_metadata", "document_id": 7, "title": "New"}
    )
    with pytest.raises(PaperlessError):
        await apply_proposal(paperless_client, db, p)
    assert p.status == ProposalStatus.pending
    assert (await db.scalar(select(AppliedChange))) is None

    # The retry now succeeds — the failed attempt left no debris behind.
    patch_route.mock(return_value=Response(200, json=DOC | {"title": "New"}))
    change = await apply_proposal(paperless_client, db, p)
    assert change is not None and p.status == ProposalStatus.applied


@respx.mock
async def test_failed_revert_releases_claim_for_retry(db, paperless_client):
    """AUDIT API-F5 flip side: when the revert's paperless write fails,
    the reverted_at claim must be released — otherwise the change reads
    as reverted although paperless still holds the applied state."""
    p, change = await _make_change(
        db,
        {"kind": "update_document_metadata", "document_id": 7, "title": "Agent title"},
        before={"document": {"id": 7, "title": "scan_0001"}},
        after={"document": {"id": 7, "title": "Agent title"}},
    )
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Agent title"})
    )
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(500, text="boom")
    )
    with pytest.raises(PaperlessError):
        await revert_change(paperless_client, db, change)
    assert change.reverted_at is None  # claim released

    patch_route.mock(return_value=Response(200, json=DOC))
    await revert_change(paperless_client, db, change)
    assert change.reverted_at is not None


# ----- delete with force ----------------------------------------------


@respx.mock
async def test_revert_with_only_the_change_loaded(db, paperless_client):
    """Regression: revert_change must not depend on the caller holding
    the Proposal ORM object. A caller loading ONLY the AppliedChange
    (empty identity map) used to hit an async lazy-load on
    change.proposal after the claim refresh (MissingGreenlet) — the
    claim refresh now loads the relationship explicitly."""
    _, change = await _make_change(
        db,
        {"kind": "update_document_metadata", "document_id": 7, "title": "Agent title"},
        before={"document": {"id": 7, "title": "scan_0001"}},
        after={"document": {"id": 7, "title": "Agent title"}},
    )
    change_id = change.id
    db.expunge_all()  # simulate a fresh caller: neither object cached
    change = await db.get(AppliedChange, change_id)
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Agent title"})
    )
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC)
    )
    await revert_change(paperless_client, db, change)
    assert change.reverted_at is not None
    assert json.loads(patch_route.calls.last.request.content)["title"] == "scan_0001"


@respx.mock
async def test_delete_entity_force_detaches_then_deletes(db, paperless_client):
    """force=true on a referenced entity: documents are detached FIRST
    and journaled, so the revert can restore both entity and links."""
    respx.get(f"{PAPERLESS_URL}/api/tags/9/").mock(
        return_value=Response(200, json=_tag(9, "old-stuff"))
    )
    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(
        return_value=Response(200, json={
            "count": 2, "next": None, "all": [3, 4],
            "results": [DOC | {"id": 3}, DOC | {"id": 4}],
        })
    )
    bulk = respx.post(f"{PAPERLESS_URL}/api/documents/bulk_edit/").mock(
        return_value=Response(200, json={"result": "OK"})
    )
    delete_route = respx.delete(f"{PAPERLESS_URL}/api/tags/9/").mock(
        return_value=Response(204)
    )
    p = await _make_proposal(
        db, {"kind": "delete_entity", "entity_type": "tag", "entity_id": 9,
             "force": True}
    )
    change = await apply_proposal(paperless_client, db, p)
    body = json.loads(bulk.calls.last.request.content)
    assert body == {"documents": [3, 4], "method": "modify_tags",
                    "parameters": {"add_tags": [], "remove_tags": [9]}}
    assert delete_route.called
    assert change.paperless_before["documents_detached"] == [3, 4]
    assert change.paperless_after == {"deleted": True, "documents_detached": [3, 4]}


# ----- reverts that recreate entities ---------------------------------


@respx.mock
async def test_revert_update_entity_restores_rule_fields_only(db, paperless_client):
    """Reverting an entity update restores the journaled rule fields
    (name/match/algorithm/sensitivity) — not incidental snapshot keys
    like document_count, which paperless computes itself."""
    p, change = await _make_change(
        db,
        {"kind": "update_entity", "entity_type": "correspondent", "entity_id": 5,
         "name": "New Name"},
        before={"entity": {"id": 5, "name": "Old Name", "match": "old",
                           "matching_algorithm": 1, "is_insensitive": True,
                           "document_count": 12}},
        after={"entity": {"id": 5, "name": "New Name", "match": "old",
                          "matching_algorithm": 1, "is_insensitive": True}},
    )
    respx.get(f"{PAPERLESS_URL}/api/correspondents/5/").mock(
        return_value=Response(200, json=_tag(5, "New Name", match="old",
                                             matching_algorithm=1))
    )
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/correspondents/5/").mock(
        return_value=Response(200, json=_tag(5, "Old Name", match="old",
                                             matching_algorithm=1))
    )
    await revert_change(paperless_client, db, change)
    body = json.loads(patch_route.calls.last.request.content)
    assert body == {"name": "Old Name", "match": "old", "matching_algorithm": 1,
                    "is_insensitive": True}


@respx.mock
async def test_revert_merge_recreates_source_and_reassigns_documents(db, paperless_client):
    """Undoing a merge must bring the deleted source entity back (new id)
    AND move the journaled documents off the target back onto it — a
    revert that recreates an empty entity would silently lose the
    assignment history."""
    p, change = await _make_change(
        db,
        {"kind": "merge_entities", "entity_type": "tag", "source_id": 2, "target_id": 1},
        before={
            "source_entity": {"id": 2, "name": "altpapier", "match": "alt",
                              "matching_algorithm": 1, "is_insensitive": True},
            "target_entity": {"id": 1, "name": "papier"},
            "documents_reassigned": [11, 12],
        },
        after={"merged_into": {"id": 1, "name": "papier"},
               "documents_reassigned": [11, 12]},
    )
    # revert_is_noop probes by name: "altpapier" must not exist anymore.
    respx.get(f"{PAPERLESS_URL}/api/tags/").mock(
        return_value=Response(200, json={"count": 1, "next": None,
                                         "results": [_tag(1, "papier")]})
    )
    create_route = respx.post(f"{PAPERLESS_URL}/api/tags/").mock(
        return_value=Response(201, json=_tag(33, "altpapier", match="alt",
                                             matching_algorithm=1))
    )
    bulk = respx.post(f"{PAPERLESS_URL}/api/documents/bulk_edit/").mock(
        return_value=Response(200, json={"result": "OK"})
    )
    await revert_change(paperless_client, db, change)
    created = json.loads(create_route.calls.last.request.content)
    assert created["name"] == "altpapier" and created["matching_algorithm"] == 1
    body = json.loads(bulk.calls.last.request.content)
    assert body == {"documents": [11, 12], "method": "modify_tags",
                    "parameters": {"add_tags": [33], "remove_tags": [1]}}
    assert change.reverted_at is not None


@respx.mock
async def test_revert_delete_recreates_entity_and_reattaches(db, paperless_client):
    """Undoing a forced delete recreates the entity and reattaches the
    documents that were detached — for single-valued types via the bulk
    set method, pointing at the NEW id."""
    p, change = await _make_change(
        db,
        {"kind": "delete_entity", "entity_type": "correspondent", "entity_id": 5,
         "force": True},
        before={"entity": {"id": 5, "name": "Kraxi", "match": "kraxi",
                           "matching_algorithm": 1, "is_insensitive": True},
                "documents_detached": [3, 4]},
        after={"deleted": True, "documents_detached": [3, 4]},
    )
    respx.get(f"{PAPERLESS_URL}/api/correspondents/").mock(
        return_value=Response(200, json={"count": 0, "next": None, "results": []})
    )
    create_route = respx.post(f"{PAPERLESS_URL}/api/correspondents/").mock(
        return_value=Response(201, json=_tag(66, "Kraxi", match="kraxi",
                                             matching_algorithm=1))
    )
    bulk = respx.post(f"{PAPERLESS_URL}/api/documents/bulk_edit/").mock(
        return_value=Response(200, json={"result": "OK"})
    )
    await revert_change(paperless_client, db, change)
    assert json.loads(create_route.calls.last.request.content)["name"] == "Kraxi"
    body = json.loads(bulk.calls.last.request.content)
    assert body == {"documents": [3, 4], "method": "set_correspondent",
                    "parameters": {"correspondent": 66}}


@respx.mock
async def test_revert_merge_noop_when_source_name_reappeared(db, paperless_client):
    """Someone already recreated the merged-away entity by name: the
    revert has nothing to restore and must refuse instead of creating a
    duplicate."""
    p, change = await _make_change(
        db,
        {"kind": "merge_entities", "entity_type": "tag", "source_id": 2, "target_id": 1},
        before={"source_entity": {"id": 2, "name": "altpapier"},
                "target_entity": {"id": 1, "name": "papier"},
                "documents_reassigned": []},
        after={"merged_into": {"id": 1, "name": "papier"}, "documents_reassigned": []},
    )
    respx.get(f"{PAPERLESS_URL}/api/tags/").mock(
        return_value=Response(200, json={
            "count": 2, "next": None,
            "results": [_tag(1, "papier"), _tag(40, "Altpapier")],  # case-insensitive hit
        })
    )
    assert await revert_is_noop(paperless_client, p, change) is True
    with pytest.raises(ApplyError, match="nothing to undo"):
        await revert_change(paperless_client, db, change)
    assert change.reverted_at is None
