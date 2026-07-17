"""API router tests: FastAPI app over ASGI transport with overridden
dependencies (test DB session + respx-mocked paperless)."""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from app.api.deps import get_paperless
from app.db.models import AgentKind, Proposal, ProposalStatus, Session
from app.db.session import get_session
from app.main import create_app
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


@pytest.fixture
async def client(db, paperless_client):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_paperless] = lambda: paperless_client
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_proposal(db, status=ProposalStatus.pending) -> Proposal:
    s = Session(agent_kind=AgentKind.document)
    db.add(s)
    await db.flush()
    p = Proposal(
        session_id=s.id,
        kind="update_document_metadata",
        agent_payload={
            "kind": "update_document_metadata",
            "document_id": 7,
            "reason": "r",
            "title": "Agent title",
        },
        status=status,
    )
    db.add(p)
    await db.commit()
    return p


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_proposal_list_and_detail(client, db):
    p = await _seed_proposal(db)
    r = await client.get("/api/proposals")
    assert r.status_code == 200 and len(r.json()) == 1
    r = await client.get(f"/api/proposals/{p.id}")
    assert r.json()["agent_payload"]["title"] == "Agent title"
    assert (await client.get("/api/proposals/999")).status_code == 404


async def test_patch_replaces_and_validates(client, db):
    p = await _seed_proposal(db)
    # Full replacement payload: dropping the agent's `title` is possible.
    r = await client.patch(
        f"/api/proposals/{p.id}",
        json={"user_payload": {"document_id": 7, "reason": "r", "add_tags": [1]}},
    )
    assert r.status_code == 200
    body = r.json()
    assert "title" not in body["user_payload"]  # agent field dropped
    assert body["user_payload"]["add_tags"] == [1]
    assert body["user_payload"]["kind"] == "update_document_metadata"  # enforced
    assert body["agent_payload"]["title"] == "Agent title"  # immutable

    # Invalid payloads are rejected.
    r = await client.patch(
        f"/api/proposals/{p.id}",
        json={"user_payload": {"document_id": 7, "bogus_field": 1}},
    )
    assert r.status_code == 422

    # Discard edits.
    r = await client.patch(f"/api/proposals/{p.id}", json={"user_payload": None})
    assert r.json()["user_payload"] is None


async def test_approve_reject_conflicts(client, db):
    p = await _seed_proposal(db)
    assert (await client.post(f"/api/proposals/{p.id}/approve")).json()["status"] == "approved"
    # Approving again conflicts.
    assert (await client.post(f"/api/proposals/{p.id}/approve")).status_code == 409
    assert (await client.post(f"/api/proposals/{p.id}/reject")).json()["status"] == "rejected"
    # Rejected proposals cannot be edited.
    r = await client.patch(f"/api/proposals/{p.id}", json={"user_payload": {"title": "x"}})
    assert r.status_code == 409


@respx.mock
async def test_apply_reports_applied_flag(client, db):
    """Regression: the response of POST /apply must reflect the freshly
    created journal entry (applied=true), not stale relationship state."""
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(return_value=Response(200, json=DOC))
    respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Agent title"})
    )
    p = await _seed_proposal(db)
    r = await client.post(f"/api/proposals/{p.id}/apply")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "applied"
    assert body["applied"] is True
    assert body["reverted"] is False


@respx.mock
async def test_apply_then_revert_roundtrip(client, db):
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(return_value=Response(200, json=DOC))
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Agent title"})
    )
    p = await _seed_proposal(db)
    assert (await client.post(f"/api/proposals/{p.id}/apply")).status_code == 200
    r = await client.post(f"/api/proposals/{p.id}/revert")
    assert r.status_code == 200
    assert r.json()["reverted"] is True

    import json

    # Last PATCH restored the original title from the journal snapshot.
    restored = json.loads(patch_route.calls.last.request.content)
    assert restored["title"] == "scan_0001"
    # Reverting twice conflicts.
    assert (await client.post(f"/api/proposals/{p.id}/revert")).status_code == 409


async def test_revert_never_applied_conflicts(client, db):
    p = await _seed_proposal(db)
    assert (await client.post(f"/api/proposals/{p.id}/revert")).status_code == 409


async def test_sessions_list_and_detail(client, db):
    p = await _seed_proposal(db)
    r = await client.get("/api/sessions")
    assert r.status_code == 200 and len(r.json()) == 1
    assert r.json()[0]["proposal_count"] == 1
    r = await client.get(f"/api/sessions/{p.session_id}")
    body = r.json()
    assert [x["id"] for x in body["proposals"]] == [p.id]
    assert body["proposals"][0]["agent_payload"]["title"] == "Agent title"
    assert (await client.get("/api/sessions/999")).status_code == 404


async def test_session_detail_exposes_transcript_not_raw_history(client, db):
    s = Session(
        agent_kind=AgentKind.document,
        message_history=[
            {
                "kind": "request",
                "parts": [
                    {"part_kind": "system-prompt", "content": "SECRET"},
                    {"part_kind": "user-prompt", "content": "Process document id=7."},
                ],
            },
            {"kind": "response", "parts": [{"part_kind": "text", "content": "hi"}]},
        ],
    )
    db.add(s)
    await db.commit()
    body = (await client.get(f"/api/sessions/{s.id}")).json()
    assert "message_history" not in body
    assert [t["role"] for t in body["transcript"]] == ["user", "agent"]
    assert body["transcript"][0]["origin"] == "pipeline"
    assert "SECRET" not in str(body["transcript"])


async def _queue_items(db, stage=None):
    from sqlalchemy import select

    from app.db.models import QueueItem

    q = select(QueueItem).order_by(QueueItem.id)
    if stage:
        q = q.where(QueueItem.stage == stage)
    return (await db.scalars(q)).all()


async def test_steering_message_schedules_turn(client, db):
    from app.db.models import SessionPhase, SessionStatus

    s = Session(agent_kind=AgentKind.document, phase=SessionPhase.done)
    db.add(s)
    await db.commit()

    r = await client.post(f"/api/sessions/{s.id}/messages", json={"content": "use German"})
    assert r.status_code == 202
    assert r.json()["status"] == "running"  # busy immediately
    items = await _queue_items(db, "steering")
    assert len(items) == 1
    assert items[0].args == {"session_id": s.id, "content": "use German"}
    assert items[0].lane.value == "interactive"
    assert items[0].state.value == "pending"

    # Concurrent sends 409 while the turn runs.
    r = await client.post(f"/api/sessions/{s.id}/messages", json={"content": "more"})
    assert r.status_code == 409

    # Empty messages rejected.
    s.status = SessionStatus.idle
    await db.commit()
    r = await client.post(f"/api/sessions/{s.id}/messages", json={"content": "  "})
    assert r.status_code == 422


async def test_steering_blocked_during_gate_phases(client, db):
    from app.db.models import SessionPhase

    s = Session(agent_kind=AgentKind.document, phase=SessionPhase.ocr_review)
    db.add(s)
    await db.commit()
    r = await client.post(f"/api/sessions/{s.id}/messages", json={"content": "hi"})
    assert r.status_code == 409


async def test_ocr_rerun_from_gate(client, db):
    from app.db.models import SessionPhase

    s = Session(
        agent_kind=AgentKind.document,
        entity_id=7,
        phase=SessionPhase.ocr_review,
        params={"redo_ocr": True},
    )
    db.add(s)
    await db.commit()

    r = await client.post(
        f"/api/sessions/{s.id}/ocr/rerun", json={"instructions": "higher DPI, mind the stamp"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "ocr_running"
    assert body["params"]["ocr_instructions"] == "higher DPI, mind the stamp"
    items = await _queue_items(db, "reocr")
    assert len(items) == 1
    assert items[0].args["instructions"] == "higher DPI, mind the stamp"

    # Only available at the gate.
    r = await client.post(f"/api/sessions/{s.id}/ocr/rerun", json={})
    assert r.status_code == 409


async def test_sse_stream_delivers_published_events(client, db):
    """Drives the endpoint's generator directly: httpx's ASGI transport
    buffers whole bodies, which never terminates for an SSE stream."""
    import asyncio
    import json

    from app.api.routes.sessions import session_events
    from app.services.events import bus

    s = Session(agent_kind=AgentKind.document)
    db.add(s)
    await db.commit()

    resp = await session_events(s.id, db)
    assert resp.media_type == "text/event-stream"
    gen = resp.body_iterator
    try:
        hello = json.loads((await asyncio.wait_for(anext(gen), 5)).removeprefix("data: "))
        assert hello["type"] == "hello"
        bus.publish(s.id, "phase_changed", phase="done")
        ev = json.loads((await asyncio.wait_for(anext(gen), 5)).removeprefix("data: "))
        assert ev["type"] == "phase_changed" and ev["phase"] == "done"
    finally:
        await gen.aclose()
    assert not bus._subs.get(s.id)  # unsubscribed on close

    # Unknown sessions 404 before any stream starts.
    assert (await client.get("/api/sessions/999/events")).status_code == 404


async def test_analyze_creates_queued_session(client, db):
    r = await client.post(
        "/api/sessions/analyze/document/7", json={"redo_ocr": True, "instructions": "hi"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "queued"
    assert body["params"] == {"redo_ocr": True, "instructions": "hi"}
    items = await _queue_items(db, "start")
    assert len(items) == 1 and items[0].session_id == body["id"]


async def test_analyze_taxonomy_entity(client, db):
    r = await client.post(
        "/api/sessions/analyze/correspondent/3", json={"instructions": "check for dupes"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["agent_kind"] == "correspondent"
    assert body["entity_type"] == "correspondent"
    assert body["phase"] == "queued"
    assert (await _queue_items(db, "start"))[0].session_id == body["id"]
    # Unknown taxonomy types are rejected.
    assert (await client.post("/api/sessions/analyze/banana/1", json={})).status_code == 422


@respx.mock
async def test_ocr_gate_flow(client, db):
    """Gate: review data excludes similarity; accepting user-fixed
    content writes it to paperless (journaled) and schedules analysis."""
    from app.db.models import (
        EntityType,
        OcrResult,
        SessionPhase,
    )

    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(return_value=Response(200, json=DOC))
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"content": "fixed text"})
    )

    s = Session(
        agent_kind=AgentKind.document,
        entity_type=EntityType.document,
        entity_id=7,
        phase=SessionPhase.ocr_review,
        params={"redo_ocr": True},
    )
    db.add(s)
    db.add(
        OcrResult(
            document_id=7, checksum="x", model="m", prompt_version=1,
            pages=["ocr text"], text="ocr text", similarity=0.42,
        )
    )
    await db.commit()

    r = await client.get(f"/api/sessions/{s.id}/ocr")
    assert r.status_code == 200
    body = r.json()
    assert body["previous_content"] == "old content"
    assert body["ocr_text"] == "ocr text"
    assert "similarity" not in str(body)

    r = await client.post(f"/api/sessions/{s.id}/ocr/gate", json={"content": "fixed text"})
    assert r.status_code == 200
    assert r.json()["params"]["ocr_gate"] == "accepted"
    import json as _json

    assert _json.loads(patch_route.calls.last.request.content)["content"] == "fixed text"
    assert len(await _queue_items(db, "analysis")) == 1
    # The internal proposal is journaled and applied, with the user fix
    # preserved separately from the raw OCR text.
    from sqlalchemy import select

    from app.db.models import Proposal

    prop = await db.scalar(select(Proposal).where(Proposal.session_id == s.id))
    assert prop.status.value == "applied"
    assert prop.agent_payload["content"] == "ocr text"
    assert prop.user_payload["content"] == "fixed text"
    # Gate cannot be resolved twice.
    assert (
        await client.post(f"/api/sessions/{s.id}/ocr/gate", json={"content": None})
    ).status_code == 409


@respx.mock
async def test_ocr_gate_keep_existing(client, db):
    from app.db.models import (
        EntityType,
        OcrResult,
        SessionPhase,
    )

    s = Session(
        agent_kind=AgentKind.document, entity_type=EntityType.document,
        entity_id=7, phase=SessionPhase.ocr_review, params={"redo_ocr": True},
    )
    db.add(s)
    db.add(
        OcrResult(
            document_id=7, checksum="x", model="m", prompt_version=1,
            pages=["t"], text="t",
        )
    )
    await db.commit()

    r = await client.post(f"/api/sessions/{s.id}/ocr/gate", json={"content": None})
    assert r.status_code == 200
    assert r.json()["params"]["ocr_gate"] == "kept_existing"
    from sqlalchemy import select

    from app.db.models import Proposal

    assert (await db.scalar(select(Proposal).where(Proposal.session_id == s.id))) is None


def _entity_page(*items):
    return Response(200, json={"count": len(items), "next": None, "results": list(items)})


def _tag(id, name, count=0, inbox=False):
    return {"id": id, "name": name, "document_count": count, "match": "",
            "matching_algorithm": 0, "is_inbox_tag": inbox}


@respx.mock
async def test_merge_candidates_route(client):
    respx.get(f"{PAPERLESS_URL}/api/correspondents/").mock(
        return_value=_entity_page(
            _tag(1, "Kraxi", 5), _tag(2, "Kraxi GmbH", 1), _tag(3, "Finanzamt", 2)
        )
    )
    r = await client.get("/api/entities/correspondent/merge-candidates")
    assert r.status_code == 200
    pairs = r.json()
    assert len(pairs) == 1
    assert pairs[0]["target"]["name"] == "Kraxi"
    assert pairs[0]["source"]["name"] == "Kraxi GmbH"
    assert (await client.get("/api/entities/banana/merge-candidates")).status_code == 422


@respx.mock
async def test_job_campaign_lifecycle(client, db):
    """Create a campaign from explicit ids -> sessions + queue items;
    cancel -> pending items cancelled."""
    r = await client.post(
        "/api/jobs",
        json={"document_ids": [11, 12], "redo_ocr": False, "apply_policy": "auto"},
    )
    assert r.status_code == 200
    job = r.json()
    assert job["total"] == 2 and job["status"] == "queued"

    from sqlalchemy import select

    from app.db.models import QueueItem
    from app.db.models import Session as DbSession

    sessions = (await db.scalars(select(DbSession))).all()
    assert [s.entity_id for s in sessions] == [11, 12]
    assert all(s.params["apply_policy"] == "auto" for s in sessions)
    items = (await db.scalars(select(QueueItem))).all()
    assert len(items) == 2 and all(i.lane.value == "batch" for i in items)

    detail = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert len(detail["sessions"]) == 2

    r = await client.post(f"/api/jobs/{job['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    items = (await db.scalars(select(QueueItem))).all()
    assert all(i.state.value == "cancelled" for i in items)
    # Cancelling again conflicts.
    assert (await client.post(f"/api/jobs/{job['id']}/cancel")).status_code == 409


@respx.mock
async def test_job_inbox_campaign_resolves_inbox_tags(client, db):
    respx.get(f"{PAPERLESS_URL}/api/tags/").mock(
        return_value=_entity_page(
            _tag(1, "Inbox", 2, inbox=True), _tag(2, "Steuern", 5)
        )
    )
    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(
        return_value=Response(
            200,
            json={"count": 2, "next": None, "all": [21, 22],
                  "results": [DOC | {"id": 21}, DOC | {"id": 22}]},
        )
    )
    r = await client.post("/api/jobs", json={"inbox": True})
    assert r.status_code == 200
    assert r.json()["total"] == 2
    # Empty selection is rejected.
    assert (await client.post("/api/jobs", json={})).status_code == 422


async def test_webhook_requires_secret_and_extracts_ids(client, db, monkeypatch):
    from app.config import reset_settings_cache

    # Unconfigured -> hidden.
    r = await client.post("/api/webhooks/paperless", json={"document_id": 5})
    assert r.status_code == 404

    monkeypatch.setenv("PLLM_WEBHOOK__SECRET", "s3cret")
    reset_settings_cache()
    try:
        r = await client.post("/api/webhooks/paperless", json={"document_id": 5})
        assert r.status_code == 403  # missing token

        headers = {"X-PLLM-Token": "s3cret"}
        r = await client.post(
            "/api/webhooks/paperless", json={"document_id": 5}, headers=headers
        )
        assert r.status_code == 202
        assert r.json()["queued_documents"] == [5]

        r = await client.post(
            "/api/webhooks/paperless",
            json={"url": "http://paperless/documents/77/"},
            headers=headers,
        )
        assert r.json()["queued_documents"] == [77]

        r = await client.post("/api/webhooks/paperless", json={}, headers=headers)
        assert r.status_code == 422
    finally:
        reset_settings_cache()

    from sqlalchemy import select

    from app.db.models import QueueItem

    items = (await db.scalars(select(QueueItem))).all()
    assert len(items) == 2


async def test_stats_endpoint(client, db):
    await _seed_proposal(db)
    r = await client.get("/api/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["pending_proposals"] == 1
    assert body["active_sessions"] == 0
