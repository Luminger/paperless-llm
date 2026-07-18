"""API router tests: FastAPI app over ASGI transport with overridden
dependencies (test DB session + respx-mocked paperless)."""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response
from sqlalchemy import select

from app.api.deps import get_paperless
from app.db.models import AgentKind, EntityType, Proposal, ProposalStatus, Session
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
    assert r.status_code == 200
    envelope = r.json()
    assert envelope["count"] == 1 and len(envelope["results"]) == 1
    r = await client.get(f"/api/proposals/{p.id}")
    assert r.json()["agent_payload"]["title"] == "Agent title"
    assert (await client.get("/api/proposals/999")).status_code == 404


async def test_patch_replaces_and_validates(client, db):
    p = await _seed_proposal(db)
    # Full replacement payload: dropping the agent's `title` is possible.
    r = await client.patch(
        f"/api/proposals/{p.id}",
        json={"user_payload": {"document_id": 7, "add_tags": [1]}},
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


async def test_settled_proposals_cannot_be_edited(client, db):
    """Only pending proposals are editable; settled ones are history."""
    from app.db.models import ProposalStatus

    p = await _seed_proposal(db)
    p.status = ProposalStatus.superseded
    await db.commit()
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
    assert body["params"]["redo_ocr"] is True
    assert body["params"]["instructions"] == "hi"
    steps = await _steps(db, "ocr")
    assert len(steps) == 1 and steps[0].session_id == body["id"]
    # Even a single analysis is a tracked job on the interactive lane.
    assert steps[0].lane.value == "interactive"
    from app.db.models import Job
    from app.db.models import Session as Sess

    sess = await db.get(Sess, body["id"])
    assert sess.job_id is not None
    job = await db.get(Job, sess.job_id)
    assert job.kind == "analyze" and job.total == 1


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


@respx.mock
async def test_ocr_only_gate_ends_pipeline(client, db):
    """bulk_ocr sessions stop at the gate: resolving it writes content
    but schedules NO analysis, and the session derives to done."""
    from app.db.models import (
        EntityType,
        OcrResult,
        SessionPhase,
        Step,
        StepKind,
        StepState,
    )

    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(return_value=Response(200, json=DOC))
    respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"content": "ocr text"})
    )

    s = Session(
        agent_kind=AgentKind.document,
        entity_type=EntityType.document,
        entity_id=7,
        phase=SessionPhase.ocr_review,
        params={"redo_ocr": True, "ocr_only": True},
    )
    db.add(s)
    db.add(
        OcrResult(
            document_id=7, checksum="x", model="m", prompt_version=1,
            pages=["ocr text"], text="ocr text",
        )
    )
    await db.commit()
    gate = Step(
        session_id=s.id, kind=StepKind.ocr, state=StepState.awaiting_user,
        input={"ocr_only": True},
    )
    db.add(gate)
    await db.commit()

    r = await client.post(
        f"/api/sessions/{s.id}/steps/{gate.id}/resolve", json={"content": "ocr text"}
    )
    assert r.status_code == 200
    assert await _steps(db, "analysis") == []
    await db.refresh(s)
    assert s.phase == SessionPhase.done


@respx.mock
async def test_ocr_only_job_creation(client, db):
    """POST /api/jobs with ocr_only makes a bulk_ocr job whose steps are
    OCR-only-marked, never followed by analysis."""
    from sqlalchemy import select

    from app.db.models import Job, Step, StepKind

    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(return_value=Response(200, json=DOC))
    r = await client.post("/api/jobs", json={"document_ids": [7], "ocr_only": True})
    assert r.status_code == 200
    assert r.json()["kind"] == "bulk_ocr"

    job = await db.get(Job, r.json()["id"])
    assert job.params["ocr_only"] is True
    session = await db.scalar(select(Session).where(Session.job_id == job.id))
    assert session.params["ocr_only"] is True
    step = await db.scalar(select(Step).where(Step.session_id == session.id))
    assert step.kind == StepKind.ocr
    assert step.input["ocr_only"] is True


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
    assert "inbox" in r.json()["detail"]["message"].lower()


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


async def test_error_shape_is_uniform(client):
    """Every error body is {"detail": {"code", "message", ...}}."""
    r = await client.get("/api/proposals/99999")
    assert r.status_code == 404
    d = r.json()["detail"]
    assert d["code"] == "not_found" and "proposal" in d["message"]

    # Pydantic validation errors share the shape.
    r = await client.post("/api/jobs", json={"tag_id": "not-a-number"})
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "validation" and d["message"]


async def test_jobs_list_envelope(client):
    r = await client.get("/api/jobs")
    body = r.json()
    assert set(body) == {"count", "page", "page_size", "results"}


async def test_settings_overview_has_no_secrets(client):
    r = await client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["llm_agent"]["model"]
    assert body["database"] in ("sqlite", "postgresql")
    # Secrets never leave the server — only presence indicators.
    flat = str(body)
    assert "api_key" not in flat and "secret" not in flat and "token\":" not in flat
    assert body["paperless"]["auth"] in ("token", "credentials", "none")
    assert isinstance(body["webhook"]["enabled"], bool)


async def test_unfinished_includes_sessions_with_pending_proposals(client, db):
    """A finished analysis whose proposals await review still needs the
    user — it must not vanish from the dashboard."""
    from app.db.models import Session as Sess
    from app.db.models import SessionPhase, SessionStatus

    p = await _seed_proposal(db)  # pending proposal on session
    s = await db.get(Sess, p.session_id)
    s.phase = SessionPhase.done
    s.status = SessionStatus.idle
    await db.commit()

    body = (await client.get("/api/sessions?unfinished=true")).json()
    assert any(item["id"] == p.session_id for item in body["results"])

    # Once settled (superseded by a revision), the session is finished.
    from app.db.models import Proposal as Prop
    from app.db.models import ProposalStatus

    row = await db.get(Prop, p.id)
    row.status = ProposalStatus.superseded
    await db.commit()
    body = (await client.get("/api/sessions?unfinished=true")).json()
    assert not any(item["id"] == p.session_id for item in body["results"])


async def test_entity_analysis_is_a_tracked_job(client, db):
    """Taxonomy reviews are tracked jobs too (total=1, interactive)."""
    r = await client.post("/api/sessions/analyze/correspondent/3", json={})
    assert r.status_code == 200
    from app.db.models import Job
    from app.db.models import Session as Sess

    sess = await db.get(Sess, r.json()["id"])
    assert sess.job_id is not None
    job = await db.get(Job, sess.job_id)
    assert job.kind == "analyze_entity" and job.total == 1
    assert job.params["entity_type"] == "correspondent"


async def test_task_scheduling_is_audited(client, db):
    """Step scheduling shows up in the audit log; the 'changes' filter
    stays free of it."""
    await client.post("/api/sessions/analyze/document/7", json={"redo_ocr": False})

    tasks = (await client.get("/api/audit?kind=task")).json()
    assert tasks["count"] >= 1
    entry = tasks["results"][0]
    assert entry["action"] == "scheduled"
    assert entry["detail"]["step_kind"] == "analysis"
    assert entry["detail"]["lane"] == "interactive"

    # Job creation is audited for singles too.
    jobs = (await client.get("/api/audit?kind=job")).json()
    assert any(e["detail"].get("job_kind") == "analyze" for e in jobs["results"])

    # Data-changes view excludes scheduling noise.
    changes = (await client.get("/api/audit?kind=changes")).json()
    assert all(e["kind"] != "task" for e in changes["results"])


async def test_all_api_timestamps_carry_utc_offset(client, db):
    """Storage is UTC; SQLite drops the offset — the contract re-stamps
    it so clients can render in any timezone."""
    r = await client.post("/api/sessions/analyze/document/7", json={"redo_ocr": False})
    body = r.json()
    assert body["created_at"].endswith("+00:00")
    detail = (await client.get(f"/api/sessions/{body['id']}")).json()
    step = detail["steps"][0]
    assert step["created_at"].endswith("+00:00")
    audit = (await client.get("/api/audit?page_size=1")).json()
    assert audit["results"][0]["ts"].endswith("+00:00")
    jobs = (await client.get("/api/jobs")).json()
    assert jobs["results"][0]["created_at"].endswith("+00:00")


async def test_prefs_roundtrip_and_partial_update(client):
    """Prefs persist server-side; partial updates merge; unknown values
    are rejected."""
    body = (await client.get("/api/prefs")).json()
    assert body["date_format"] == "system"
    assert body["time_format"] == "24h-seconds"
    assert body["time_zone"] == "system"
    assert body["agent_prompt_addition"] == ""

    r = await client.put(
        "/api/prefs", json={"date_format": "eu", "time_zone": "Europe/Berlin"}
    )
    assert r.status_code == 200
    assert r.json()["date_format"] == "eu"
    assert r.json()["time_zone"] == "Europe/Berlin"
    assert r.json()["time_format"] == "24h-seconds"  # untouched

    # Partial update keeps earlier values.
    await client.put("/api/prefs", json={"time_format": "12h"})
    body = (await client.get("/api/prefs")).json()
    assert body["date_format"] == "eu" and body["time_format"] == "12h"

    # Typed: garbage is refused.
    assert (
        await client.put("/api/prefs", json={"date_format": "stardate"})
    ).status_code == 422


async def _seed_stepped_proposal(db, user_payload=None) -> Proposal:
    """A proposal that came from a real step — the decision loop only
    continues those."""
    from app.db.models import QueueLane, Step, StepKind, StepState

    s = Session(agent_kind=AgentKind.document, entity_type=EntityType.document, entity_id=7)
    db.add(s)
    await db.flush()
    step = Step(
        session_id=s.id, kind=StepKind.analysis, state=StepState.succeeded,
        lane=QueueLane.interactive, max_attempts=1,
    )
    db.add(step)
    await db.flush()
    p = Proposal(
        session_id=s.id,
        step_id=step.id,
        kind="update_document_metadata",
        agent_payload={
            "kind": "update_document_metadata",
            "document_id": 7,
            "title": "Agent title",
        },
        user_payload=user_payload,
        status=ProposalStatus.pending,
    )
    db.add(p)
    await db.commit()
    return p


@respx.mock
async def test_apply_continues_the_session(client, db):
    """The decision loop: applying a proposal automatically queues a
    continuation turn that tells the agent what the user did."""
    from app.db.models import Step, StepKind

    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(return_value=Response(200, json=DOC))
    respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Agent title"})
    )
    p = await _seed_stepped_proposal(db)
    sid, pid = p.session_id, p.id

    r = await client.post(f"/api/proposals/{pid}/apply")
    assert r.json()["status"] == "applied"

    chats = (
        await db.scalars(
            select(Step).where(Step.session_id == sid, Step.kind == StepKind.chat)
        )
    ).all()
    assert len(chats) == 1
    assert chats[0].input["auto"] is True
    assert chats[0].input["content"].startswith("The user accepted your proposal")


@respx.mock
async def test_apply_with_edits_tells_the_agent_the_final_values(client, db):
    from app.db.models import Step, StepKind

    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(return_value=Response(200, json=DOC))
    respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "User title"})
    )
    p = await _seed_stepped_proposal(
        db,
        user_payload={
            "kind": "update_document_metadata",
            "document_id": 7,
            "title": "User title",
        },
    )
    sid = p.session_id

    await client.post(f"/api/proposals/{p.id}/apply")
    chat = await db.scalar(
        select(Step).where(Step.session_id == sid, Step.kind == StepKind.chat)
    )
    assert chat is not None
    assert chat.input["content"].startswith("The user edited your proposal")
    assert "User title" in chat.input["content"]  # the applied values travel


@respx.mock
async def test_no_continuation_while_other_proposals_are_open(client, db):
    from app.db.models import Step, StepKind

    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(return_value=Response(200, json=DOC))
    respx.patch(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"title": "Agent title"})
    )
    p = await _seed_stepped_proposal(db)
    sid = p.session_id
    # A second open proposal in the same session (legacy multi-proposal).
    db.add(
        Proposal(
            session_id=sid,
            step_id=p.step_id,
            kind="update_document_metadata",
            agent_payload={"kind": "update_document_metadata", "document_id": 7,
                           "created": "2020-01-01"},
            status=ProposalStatus.pending,
        )
    )
    await db.commit()

    await client.post(f"/api/proposals/{p.id}/apply")
    chats = (
        await db.scalars(
            select(Step).where(Step.session_id == sid, Step.kind == StepKind.chat)
        )
    ).all()
    assert chats == []  # the user still has a decision to make


async def test_prompt_tuning_roundtrip(client):
    """Prompt base overrides + additions persist server-side; the
    settings overview exposes the system defaults for the UI."""
    long_addition = "Focus on tax documents. " * 30  # > 255 chars (Text column)
    r = await client.put(
        "/api/prefs",
        json={"agent_prompt_addition": long_addition, "ocr_prompt_base": "Transcribe.\n"},
    )
    assert r.status_code == 200
    body = (await client.get("/api/prefs")).json()
    assert body["agent_prompt_addition"] == long_addition
    assert body["ocr_prompt_base"] == "Transcribe.\n"

    overview = (await client.get("/api/settings")).json()
    assert "ONE proposal per turn" in overview["prompt_defaults"]["agent_base"]
    assert "OCR engine" in overview["prompt_defaults"]["ocr_base"]


async def test_paperless_errors_keep_the_error_shape(client, respx_mock):
    """PaperlessError must surface as the uniform error shape, not a
    bare 500 (central exception handler)."""
    respx_mock.get("http://paperless.test/api/documents/424242/").mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )
    r = await client.get("/api/entities/documents/424242")
    assert r.status_code == 404
    body = r.json()
    assert body["detail"]["code"] == "paperless_not_found"
    assert isinstance(body["detail"]["message"], str)


async def test_entity_scope_creates_one_job_with_sessions(client, respx_mock, db):
    """Bulk taxonomy review is ONE server-side job (never a client loop)."""
    for tid, name in ((11, "alpha"), (12, "beta")):
        respx_mock.get(f"http://paperless.test/api/tags/{tid}/").mock(
            return_value=httpx.Response(
                200, json={"id": tid, "name": name, "matching_algorithm": 0,
                           "match": "", "is_inbox_tag": False,
                           "document_count": 1},
            )
        )
    r = await client.post(
        "/api/jobs", json={"entity_type": "tag", "entity_ids": [11, 12]}
    )
    assert r.status_code == 200
    job = r.json()
    assert job["kind"] == "analyze_entities"
    assert job["total"] == 2
    assert job["params"]["label"] == "2 tags"
    from sqlalchemy import select

    from app.db.models import Session as Sess
    sessions = (await db.scalars(select(Sess).where(Sess.job_id == job["id"]))).all()
    assert [s.title for s in sessions] == ["alpha", "beta"]
