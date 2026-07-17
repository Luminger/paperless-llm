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


async def test_reject_conflicts(client, db):
    p = await _seed_proposal(db)
    assert (await client.post(f"/api/proposals/{p.id}/reject")).json()["status"] == "rejected"
    # Rejecting again conflicts.
    assert (await client.post(f"/api/proposals/{p.id}/reject")).status_code == 409
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
    get_route = respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC)
    )
    patch_route = respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Agent title"})
    )
    p = await _seed_proposal(db)
    assert (await client.post(f"/api/proposals/{p.id}/apply")).status_code == 200
    # Paperless now holds the applied state.
    get_route.mock(return_value=Response(200, json=DOC | {"title": "Agent title"}))
    # revert-check: a real revert (values differ from the snapshot).
    rc = await client.get(f"/api/proposals/{p.id}/revert-check")
    assert rc.status_code == 200 and rc.json()["revert_noop"] is False
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
    assert r.status_code == 200 and r.json()["count"] == 1
    assert r.json()["results"][0]["proposal_count"] == 1
    r = await client.get(f"/api/sessions/{p.session_id}")
    body = r.json()
    assert [x["id"] for x in body["proposals"]] == [p.id]
    assert body["proposals"][0]["agent_payload"]["title"] == "Agent title"
    assert (await client.get("/api/sessions/999")).status_code == 404


async def test_session_detail_exposes_steps_with_transcript_slices(client, db):
    from app.db.models import Step, StepKind, StepState

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
    await db.flush()
    db.add(
        Step(
            session_id=s.id, kind=StepKind.analysis, state=StepState.succeeded,
            result={"message_range": [0, 2]},
        )
    )
    await db.commit()
    body = (await client.get(f"/api/sessions/{s.id}")).json()
    assert "message_history" not in body
    (step,) = body["steps"]
    assert step["kind"] == "analysis"
    assert [t["role"] for t in step["transcript"]] == ["user", "agent"]
    assert step["transcript"][0]["origin"] == "pipeline"
    assert "SECRET" not in str(step["transcript"])


async def _steps(db, kind=None):
    from sqlalchemy import select

    from app.db.models import Step

    q = select(Step).order_by(Step.id)
    if kind:
        q = q.where(Step.kind == kind)
    return (await db.scalars(q)).all()


async def test_steering_message_creates_chat_step(client, db):
    from app.db.models import SessionPhase

    s = Session(agent_kind=AgentKind.document, phase=SessionPhase.done)
    db.add(s)
    await db.commit()

    r = await client.post(f"/api/sessions/{s.id}/messages", json={"content": "use German"})
    assert r.status_code == 202
    body = r.json()
    assert body["kind"] == "chat" and body["state"] == "pending"
    assert body["input"] == {"content": "use German"}
    steps = await _steps(db, "chat")
    assert len(steps) == 1 and steps[0].lane.value == "interactive"

    # A pending/running step blocks further messages (409).
    r = await client.post(f"/api/sessions/{s.id}/messages", json={"content": "more"})
    assert r.status_code == 409

    # Empty messages rejected.
    from sqlalchemy import delete as _delete

    from app.db.models import Step as _Step

    await db.execute(_delete(_Step))
    await db.commit()
    r = await client.post(f"/api/sessions/{s.id}/messages", json={"content": "  "})
    assert r.status_code == 422


async def test_steering_blocked_during_gate(client, db):
    from app.db.models import SessionPhase, Step, StepKind, StepState

    s = Session(agent_kind=AgentKind.document, phase=SessionPhase.ocr_review)
    db.add(s)
    await db.flush()
    db.add(Step(session_id=s.id, kind=StepKind.ocr, state=StepState.awaiting_user))
    await db.commit()
    r = await client.post(f"/api/sessions/{s.id}/messages", json={"content": "hi"})
    assert r.status_code == 409


async def test_ocr_redo_from_gate(client, db):
    """'Argue with the OCR' is the generic redo action with amended input."""
    from app.db.models import EntityType, SessionPhase, Step, StepKind, StepState

    s = Session(
        agent_kind=AgentKind.document,
        entity_type=EntityType.document,
        entity_id=7,
        phase=SessionPhase.ocr_review,
        params={"redo_ocr": True},
    )
    db.add(s)
    await db.flush()
    gate = Step(session_id=s.id, kind=StepKind.ocr, state=StepState.awaiting_user)
    db.add(gate)
    await db.commit()

    r = await client.post(
        f"/api/sessions/{s.id}/steps/{gate.id}/redo",
        json={"input": {"instructions": "higher DPI, mind the stamp"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "ocr" and body["state"] == "pending"
    assert body["input"]["instructions"] == "higher DPI, mind the stamp"
    assert body["supersedes_id"] == gate.id
    await db.refresh(gate)
    assert gate.state.value == "superseded"

    # Running steps cannot be redone.
    running = Step(session_id=s.id, kind=StepKind.ocr, state=StepState.running)
    db.add(running)
    await db.commit()
    assert (
        await client.post(f"/api/sessions/{s.id}/steps/{running.id}/redo", json={})
    ).status_code == 409


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
    steps = await _steps(db, "ocr")
    assert len(steps) == 1 and steps[0].session_id == body["id"]


async def test_analyze_taxonomy_entity(client, db):
    r = await client.post(
        "/api/sessions/analyze/correspondent/3", json={"instructions": "check for dupes"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["agent_kind"] == "correspondent"
    assert body["entity_type"] == "correspondent"
    assert body["phase"] == "queued"
    assert (await _steps(db, "analysis"))[0].session_id == body["id"]
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
        Step,
        StepKind,
        StepState,
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

    gate = Step(session_id=s.id, kind=StepKind.ocr, state=StepState.awaiting_user)
    db.add(gate)
    await db.commit()
    r = await client.post(
        f"/api/sessions/{s.id}/steps/{gate.id}/resolve", json={"content": "fixed text"}
    )
    assert r.status_code == 200
    assert r.json()["result"]["resolution"] == "accepted"
    import json as _json

    assert _json.loads(patch_route.calls.last.request.content)["content"] == "fixed text"
    analysis = await _steps(db, "analysis")
    assert len(analysis) == 1 and analysis[0].input == {"gate": "accepted"}
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
        await client.post(
            f"/api/sessions/{s.id}/steps/{gate.id}/resolve", json={"content": None}
        )
    ).status_code == 409


@respx.mock
async def test_ocr_gate_keep_existing(client, db):
    from app.db.models import (
        EntityType,
        OcrResult,
        SessionPhase,
        Step,
        StepKind,
        StepState,
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

    gate = Step(session_id=s.id, kind=StepKind.ocr, state=StepState.awaiting_user)
    db.add(gate)
    await db.commit()
    r = await client.post(
        f"/api/sessions/{s.id}/steps/{gate.id}/resolve", json={"content": None}
    )
    assert r.status_code == 200
    assert r.json()["result"]["resolution"] == "kept_existing"
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
async def test_job_lifecycle(client, db):
    """Create a job from explicit ids -> sessions + queue items;
    cancel -> pending items cancelled."""
    r = await client.post(
        "/api/jobs",
        json={"document_ids": [11, 12], "redo_ocr": False, "apply_policy": "auto"},
    )
    assert r.status_code == 200
    job = r.json()
    assert job["total"] == 2 and job["status"] == "queued"

    from sqlalchemy import select

    from app.db.models import Session as DbSession
    from app.db.models import Step

    sessions = (await db.scalars(select(DbSession))).all()
    assert [s.entity_id for s in sessions] == [11, 12]
    assert all(s.params["apply_policy"] == "auto" for s in sessions)
    steps = (await db.scalars(select(Step))).all()
    assert len(steps) == 2 and all(st.lane.value == "batch" for st in steps)

    detail = (await client.get(f"/api/jobs/{job['id']}")).json()
    assert len(detail["sessions"]) == 2

    r = await client.post(f"/api/jobs/{job['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    steps = (await db.scalars(select(Step))).all()
    assert all(st.state.value == "cancelled" for st in steps)
    # Cancelling again conflicts.
    assert (await client.post(f"/api/jobs/{job['id']}/cancel")).status_code == 409


@respx.mock
async def test_job_inbox_scope_resolves_inbox_tags(client, db):
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

    from app.db.models import Step

    steps = (await db.scalars(select(Step))).all()
    assert len(steps) == 2


async def test_stats_endpoint(client, db):
    await _seed_proposal(db)
    r = await client.get("/api/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["pending_proposals"] == 1
    assert body["active_sessions"] == 0


async def test_retry_step_revives_failed(client, db):
    from app.db.models import (
        SessionPhase,
        SessionStatus,
        Step,
        StepKind,
        StepState,
    )

    s = Session(
        agent_kind=AgentKind.document,
        phase=SessionPhase.analyzing,
        status=SessionStatus.failed,
        error="ConnectError: LLM down",
    )
    db.add(s)
    await db.flush()
    step = Step(
        session_id=s.id, kind=StepKind.analysis, state=StepState.failed,
        attempt_count=3, max_attempts=3, error="ConnectError: LLM down",
        attempts=[{"attempt": 1}, {"attempt": 2}, {"attempt": 3}],
    )
    db.add(step)
    await db.commit()

    r = await client.post(f"/api/sessions/{s.id}/steps/{step.id}/retry")
    assert r.status_code == 200
    await db.refresh(step)
    assert step.state == StepState.pending
    assert step.attempt_count == 0  # fresh budget
    assert step.attempts[-1].get("manual_retry_at")  # history kept


async def test_retry_step_skips_backoff(client, db):
    from datetime import timedelta

    from app.db.models import SessionPhase, Step, StepKind, StepState, utcnow

    s = Session(agent_kind=AgentKind.document, phase=SessionPhase.analyzing)
    db.add(s)
    await db.flush()
    step = Step(
        session_id=s.id, kind=StepKind.analysis, state=StepState.pending,
        attempt_count=1, max_attempts=3,
        scheduled_at=utcnow() + timedelta(minutes=5),
    )
    db.add(step)
    await db.commit()

    # Scheduling is visible on the step for the UI.
    detail = (await client.get(f"/api/sessions/{s.id}")).json()
    assert detail["steps"][0]["scheduled_at"] is not None

    r = await client.post(f"/api/sessions/{s.id}/steps/{step.id}/retry")
    assert r.status_code == 200
    await db.refresh(step)
    assert step.scheduled_at is None

    # Succeeded steps have nothing to retry -> 409.
    done = Step(session_id=s.id, kind=StepKind.analysis, state=StepState.succeeded)
    db.add(done)
    await db.commit()
    assert (
        await client.post(f"/api/sessions/{s.id}/steps/{done.id}/retry")
    ).status_code == 409


async def _mk_sessions(db, n, entity_id=7, archived=0):
    from app.db.models import EntityType, SessionPhase, utcnow

    out = []
    for i in range(n):
        s = Session(
            agent_kind=AgentKind.document, entity_type=EntityType.document,
            entity_id=entity_id, phase=SessionPhase.done,
            archived_at=utcnow() if i < archived else None,
        )
        db.add(s)
        out.append(s)
    await db.commit()
    return out


async def test_session_list_pagination_and_entity_filter(client, db):
    await _mk_sessions(db, 7, entity_id=7)
    await _mk_sessions(db, 2, entity_id=8)

    body = (await client.get("/api/sessions?page_size=5")).json()
    assert body["count"] == 9
    assert len(body["results"]) == 5 and body["page"] == 1

    body = (await client.get("/api/sessions?page=2&page_size=5")).json()
    assert len(body["results"]) == 4

    body = (
        await client.get("/api/sessions?entity_type=document&entity_id=8&page_size=5")
    ).json()
    assert body["count"] == 2

    assert (await client.get("/api/sessions?entity_type=banana")).status_code == 422


async def test_archive_lifecycle_blocks_forward_but_not_revert(client, db):
    """Archived sessions: hidden from the active list, refuse apply and
    new steps — but applied changes remain revertible."""
    import respx as _respx
    from httpx import Response as _Resp

    from app.db.models import Step, StepKind, StepState

    (s,) = await _mk_sessions(db, 1)
    sid = s.id
    p = await _seed_proposal(db)
    p.session_id = sid
    step = Step(session_id=sid, kind=StepKind.analysis, state=StepState.failed)
    db.add(step)
    await db.commit()
    # Plain ids only from here on: the apply route expires the shared
    # test session's identity map.
    p_id, step_id = p.id, step.id

    with _respx.mock:
        get_route = _respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
            return_value=_Resp(200, json=DOC)
        )
        _respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
            return_value=_Resp(200, json=DOC | {"title": "Agent title"})
        )
        # Apply while active -> ok.
        assert (await client.post(f"/api/proposals/{p_id}/apply")).status_code == 200
        # Paperless now holds the applied state (so revert is real).
        get_route.mock(return_value=_Resp(200, json=DOC | {"title": "Agent title"}))

        # Archive.
        r = await client.post(f"/api/sessions/{sid}/archive")
        assert r.status_code == 200 and r.json()["archived_at"] is not None

        # Hidden from active list, present in archived list.
        flt = "entity_type=document&entity_id=7"
        assert (await client.get(f"/api/sessions?{flt}")).json()["count"] == 0
        assert (
            await client.get(f"/api/sessions?archived=true&{flt}")
        ).json()["count"] == 1

        # Forward actions refused.
        p2 = await _seed_proposal(db)
        p2.session_id = sid
        await db.commit()
        p2_id = p2.id
        assert (await client.post(f"/api/proposals/{p2_id}/apply")).status_code == 409
        assert (
            await client.post(f"/api/sessions/{sid}/messages", json={"content": "hi"})
        ).status_code == 409
        assert (
            await client.post(f"/api/sessions/{sid}/steps/{step_id}/retry")
        ).status_code == 409

        # Revert of the earlier applied change still works (journal).
        assert (await client.post(f"/api/proposals/{p_id}/revert")).status_code == 200

        # Unarchive restores forward actions.
        assert (await client.post(f"/api/sessions/{sid}/unarchive")).status_code == 200
        assert (await client.post(f"/api/proposals/{p2_id}/apply")).status_code == 200


@respx.mock
async def test_generic_entity_detail_route(client):
    respx.get(f"{PAPERLESS_URL}/api/correspondents/8/").mock(
        return_value=Response(200, json={
            "id": 8, "name": "Unbekannt", "document_count": 2,
            "match": "", "matching_algorithm": 0,
        })
    )
    body = (await client.get("/api/entities/correspondent/8")).json()
    assert body["name"] == "Unbekannt" and body["document_count"] == 2
    assert (await client.get("/api/entities/banana/1")).status_code == 422


async def test_meta_endpoint(client):
    body = (await client.get("/api/meta")).json()
    assert "paperless_url" in body


@respx.mock
async def test_audit_records_apply_and_revert(client, db):
    get_route = respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC)
    )
    respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Agent title"})
    )
    p = await _seed_proposal(db)
    p_id = p.id
    assert (await client.post(f"/api/proposals/{p_id}/apply")).status_code == 200
    get_route.mock(return_value=Response(200, json=DOC | {"title": "Agent title"}))
    assert (await client.post(f"/api/proposals/{p_id}/revert")).status_code == 200

    body = (await client.get("/api/audit")).json()
    assert body["count"] >= 2
    actions = [(e["kind"], e["action"]) for e in body["results"]]
    assert ("proposal", "reverted") == actions[0]  # newest first
    assert ("proposal", "applied") in actions
    applied = next(e for e in body["results"] if e["action"] == "applied")
    assert applied["detail"]["proposal_id"] == p_id


async def test_audit_records_archive_ops(client, db):
    (s,) = await _mk_sessions(db, 1)
    sid = s.id
    await client.post(f"/api/sessions/{sid}/archive")
    await client.post(f"/api/sessions/{sid}/unarchive")
    body = (await client.get("/api/audit")).json()
    actions = [(e["kind"], e["action"]) for e in body["results"]]
    assert ("session", "unarchived") == actions[0]
    assert ("session", "archived") == actions[1]


@respx.mock
async def test_sync_status_tracks_paperless_fetches(client):
    from app.paperless.client import fetch_status

    fetch_status.clear()
    respx.get(f"{PAPERLESS_URL}/api/tags/").mock(
        return_value=Response(200, json={"count": 0, "next": None, "results": []})
    )
    await client.get("/api/entities/tags")
    body = (await client.get("/api/sync/status")).json()
    tags = body["resources"]["tags"]
    assert tags["last_fetched_at"] is not None
    assert tags["in_flight"] == 0
    assert tags["last_error"] is None


async def test_stats_lifetime_counters_and_unfinished_filter(client, db):
    from app.db.models import SessionPhase, SessionStatus
    from app.services.counters import increment

    await increment(db, ocr_runs=2, llm_output_tokens=1000)
    await increment(db, ocr_runs=1, llm_output_tokens=234)
    await db.commit()

    body = (await client.get("/api/stats")).json()
    assert body["lifetime"]["ocr_runs"] == 3
    assert body["lifetime"]["llm_output_tokens"] == 1234

    # Unfinished filter: gate + failed + running stay; done/idle drops out.
    finished = Session(agent_kind=AgentKind.document, phase=SessionPhase.done,
                       status=SessionStatus.idle)
    gate = Session(agent_kind=AgentKind.document, phase=SessionPhase.ocr_review)
    failed = Session(agent_kind=AgentKind.document, phase=SessionPhase.done,
                     status=SessionStatus.failed)
    db.add_all([finished, gate, failed])
    await db.commit()

    body = (await client.get("/api/sessions?unfinished=true")).json()
    ids = [s["id"] for s in body["results"]]
    assert gate.id in ids and failed.id in ids and finished.id not in ids


@respx.mock
async def test_audit_actor_attribution_and_diff(client, db):
    """API-triggered work is attributed to the user; the applied entry
    carries the from->to diff derived from the journal."""
    get_route = respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC)
    )
    respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Agent title"})
    )
    p = await _seed_proposal(db)
    assert (await client.post(f"/api/proposals/{p.id}/apply")).status_code == 200
    get_route.mock(return_value=Response(200, json=DOC | {"title": "Agent title"}))

    body = (await client.get("/api/audit?kind=proposal")).json()
    applied = next(e for e in body["results"] if e["action"] == "applied")
    assert applied["actor"] == "user"
    assert applied["detail"]["diff"]["title"] == {"from": "scan_0001", "to": "Agent title"}


@respx.mock
async def test_paperless_traffic_logged_with_actor(client, db):
    """Every paperless call the app makes lands in the audit buffer,
    attributed to whoever caused it."""
    from app.services.paperless_log import _buffer, drain

    _buffer.clear()
    respx.get(f"{PAPERLESS_URL}/api/tags/").mock(
        return_value=Response(200, json={"count": 0, "next": None, "results": []})
    )
    await client.get("/api/entities/tags")  # user-caused fetch
    assert len(_buffer) >= 1

    n = await drain(db)
    await db.commit()
    assert n >= 1
    body = (await client.get("/api/audit?kind=paperless")).json()
    entry = body["results"][0]
    assert entry["action"] == "fetch"
    assert entry["actor"] == "user"
    assert entry["detail"]["resource"] == "tags"
    assert entry["detail"]["method"] == "GET"

    # The changes filter hides paperless traffic.
    body = (await client.get("/api/audit?kind=changes")).json()
    assert all(e["kind"] != "paperless" for e in body["results"])


@respx.mock
async def test_entity_instructions_roundtrip_and_inbox_seed(client, db):
    """Tags list seeds the inbox default once; instructions are editable
    and clearing them never re-seeds."""
    from app.services.instructions import INBOX_DEFAULT

    respx.get(f"{PAPERLESS_URL}/api/tags/").mock(
        return_value=_entity_page(
            _tag(1, "Inbox", 2, inbox=True), _tag(2, "Steuern", 5)
        )
    )
    body = (await client.get("/api/entities/tags")).json()
    inbox = next(t for t in body if t["id"] == 1)
    assert inbox["instructions"] == INBOX_DEFAULT
    assert next(t for t in body if t["id"] == 2)["instructions"] == ""

    # Edit + clear.
    r = await client.put(
        "/api/entities/tag/2/instructions", json={"instructions": "Nur für Steuerpost."}
    )
    assert r.status_code == 200
    body = (await client.get("/api/entities/tags")).json()
    assert next(t for t in body if t["id"] == 2)["instructions"] == "Nur für Steuerpost."

    await client.put("/api/entities/tag/1/instructions", json={"instructions": ""})
    body = (await client.get("/api/entities/tags")).json()
    assert next(t for t in body if t["id"] == 1)["instructions"] == ""  # stays cleared


@respx.mock
async def test_inbox_tag_cannot_be_analyzed(client, db):
    respx.get(f"{PAPERLESS_URL}/api/tags/1/").mock(
        return_value=Response(200, json=_tag(1, "Inbox", 2, inbox=True))
    )
    r = await client.post("/api/sessions/analyze/tag/1", json={})
    assert r.status_code == 422
    assert "inbox" in r.json()["detail"].lower()


@respx.mock
async def test_documents_list_filters_by_taxonomy(client):
    route = respx.get(f"{PAPERLESS_URL}/api/documents/").mock(
        return_value=Response(200, json={"count": 0, "next": None, "results": []})
    )
    await client.get("/api/entities/documents?tag_id=3&correspondent_id=8&document_type_id=2")
    params = dict(route.calls.last.request.url.params)
    assert params["tags__id__all"] == "3"
    assert params["correspondent__id"] == "8"
    assert params["document_type__id"] == "2"
