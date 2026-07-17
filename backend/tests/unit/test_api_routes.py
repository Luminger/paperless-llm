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


async def test_steering_message_schedules_turn(client, db, monkeypatch):
    from app.api.routes import sessions as sessions_route
    from app.db.models import SessionPhase, SessionStatus

    spawned = []
    monkeypatch.setattr(
        sessions_route, "_spawn", lambda coro: (spawned.append(coro), coro.close())
    )
    s = Session(agent_kind=AgentKind.document, phase=SessionPhase.done)
    db.add(s)
    await db.commit()

    r = await client.post(f"/api/sessions/{s.id}/messages", json={"content": "use German"})
    assert r.status_code == 202
    assert r.json()["status"] == "running"  # busy immediately
    assert len(spawned) == 1

    # Concurrent sends 409 while the turn runs.
    r = await client.post(f"/api/sessions/{s.id}/messages", json={"content": "more"})
    assert r.status_code == 409

    # Empty messages rejected.
    s.status = SessionStatus.idle
    await db.commit()
    r = await client.post(f"/api/sessions/{s.id}/messages", json={"content": "  "})
    assert r.status_code == 422


async def test_steering_blocked_during_gate_phases(client, db, monkeypatch):
    from app.api.routes import sessions as sessions_route
    from app.db.models import SessionPhase

    monkeypatch.setattr(sessions_route, "_spawn", lambda coro: coro.close())
    s = Session(agent_kind=AgentKind.document, phase=SessionPhase.ocr_review)
    db.add(s)
    await db.commit()
    r = await client.post(f"/api/sessions/{s.id}/messages", json={"content": "hi"})
    assert r.status_code == 409


async def test_ocr_rerun_from_gate(client, db, monkeypatch):
    from app.api.routes import sessions as sessions_route
    from app.db.models import SessionPhase

    spawned = []
    monkeypatch.setattr(
        sessions_route, "_spawn", lambda coro: (spawned.append(coro), coro.close())
    )
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
    assert len(spawned) == 1

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


async def test_analyze_creates_queued_session(client, db, monkeypatch):
    from app.api.routes import sessions as sessions_route

    spawned = []
    monkeypatch.setattr(sessions_route, "_spawn", lambda coro: (spawned.append(coro), coro.close()))
    r = await client.post(
        "/api/sessions/analyze/document/7", json={"redo_ocr": True, "instructions": "hi"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "queued"
    assert body["params"] == {"redo_ocr": True, "instructions": "hi"}
    assert len(spawned) == 1


@respx.mock
async def test_ocr_gate_flow(client, db, monkeypatch):
    """Gate: review data excludes similarity; accepting user-fixed
    content writes it to paperless (journaled) and schedules analysis."""
    from app.api.routes import sessions as sessions_route
    from app.db.models import (
        AgentKind,
        EntityType,
        OcrResult,
        Session,
        SessionPhase,
    )

    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(return_value=Response(200, json=DOC))
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"content": "fixed text"})
    )
    monkeypatch.setattr(sessions_route, "_spawn", lambda coro: coro.close())

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
async def test_ocr_gate_keep_existing(client, db, monkeypatch):
    from app.api.routes import sessions as sessions_route
    from app.db.models import (
        AgentKind,
        EntityType,
        OcrResult,
        Session,
        SessionPhase,
    )

    monkeypatch.setattr(sessions_route, "_spawn", lambda coro: coro.close())
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
