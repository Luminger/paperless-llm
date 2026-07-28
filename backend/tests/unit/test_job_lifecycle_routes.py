"""Job lifecycle API: pause/resume/cancel/retry guards, the attention
walker, and the corpus-batch endpoint's terminal state.

The guards encode "a finished job cannot be paused or cancelled" against
the DERIVED status (stored counters can be stale) — breaking them lets
the UI flip completed work back into limbo."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.api.deps import get_paperless, require_user
from app.db.models import (
    AgentKind,
    EntityType,
    Job,
    JobStatus,
    Proposal,
    ProposalStatus,
    Session,
    SessionPhase,
    SessionStatus,
    Step,
    StepKind,
    StepState,
)
from app.db.session import get_session
from app.main import create_app
from app.services.auth import CurrentUser


@pytest.fixture
async def client(db, paperless_client):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_paperless] = lambda: paperless_client
    app.dependency_overrides[require_user] = lambda: CurrentUser(name="test", role="admin")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _job_with_sessions(db, specs: list[dict]) -> tuple[Job, list[Session]]:
    """specs: [{phase, status?, step_state?}, ...] one session each."""
    job = Job(kind="bulk_analyze", total=len(specs))
    db.add(job)
    await db.flush()
    sessions = []
    for i, spec in enumerate(specs, start=1):
        s = Session(
            agent_kind=AgentKind.document,
            entity_type=EntityType.document,
            entity_id=i,
            job_id=job.id,
            phase=spec.get("phase", SessionPhase.queued),
            status=spec.get("status", SessionStatus.idle),
        )
        db.add(s)
        await db.flush()
        if "step_state" in spec:
            db.add(Step(session_id=s.id, kind=StepKind.analysis,
                        state=spec["step_state"]))
        sessions.append(s)
    await db.commit()
    return job, sessions


async def test_pause_resume_roundtrip_and_guards(client, db):
    job, _ = await _job_with_sessions(db, [{"phase": SessionPhase.queued,
                                            "step_state": StepState.pending}])
    r = await client.post(f"/api/jobs/{job.id}/pause")
    assert r.status_code == 200 and r.json()["status"] == "paused"
    # Pausing again is idempotent, not an error (double-click safety).
    assert (await client.post(f"/api/jobs/{job.id}/pause")).json()["status"] == "paused"

    r = await client.post(f"/api/jobs/{job.id}/resume")
    assert r.status_code == 200 and r.json()["status"] == "running"
    # Resuming a job that is not paused is a state error.
    assert (await client.post(f"/api/jobs/{job.id}/resume")).status_code == 409
    assert (await client.post("/api/jobs/999/pause")).status_code == 404
    assert (await client.post("/api/jobs/999/resume")).status_code == 404


async def test_completed_job_cannot_be_paused_or_cancelled(client, db):
    """The stored status still says queued — completion is DERIVED from
    the sessions, and the guards must use the derived view."""
    job, _ = await _job_with_sessions(db, [{"phase": SessionPhase.done}])
    assert job.status == JobStatus.queued  # stale stored status
    assert (await client.post(f"/api/jobs/{job.id}/pause")).status_code == 409
    assert (await client.post(f"/api/jobs/{job.id}/cancel")).status_code == 409


async def test_cancel_cancels_pending_steps_and_sticks(client, db):
    job, sessions = await _job_with_sessions(
        db,
        [{"phase": SessionPhase.queued, "step_state": StepState.pending},
         {"phase": SessionPhase.done}],
    )
    r = await client.post(f"/api/jobs/{job.id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    step = await db.scalar(select(Step).where(Step.session_id == sessions[0].id))
    assert step.state == StepState.cancelled
    # Cancelled is sticky: a second cancel is a 409, and the derived
    # status never resurrects the job.
    assert (await client.post(f"/api/jobs/{job.id}/cancel")).status_code == 409
    assert (await client.get(f"/api/jobs/{job.id}")).json()["status"] == "cancelled"


async def test_bulk_retry_targets_only_retryable_sessions(client, db):
    """Retry reruns the LAST step of failed/cancelled sessions; running
    and succeeded sessions are left alone; explicit session_ids narrow
    the selection."""
    job, sessions = await _job_with_sessions(
        db,
        [
            {"phase": SessionPhase.analyzing, "status": SessionStatus.failed,
             "step_state": StepState.failed},
            {"phase": SessionPhase.stopped, "step_state": StepState.cancelled},
            {"phase": SessionPhase.done, "step_state": StepState.succeeded},
        ],
    )
    r = await client.post(f"/api/jobs/{job.id}/retry", json={})
    assert r.status_code == 200
    assert r.json()["retried"] == 2  # the failed and the cancelled one
    for s in (sessions[0], sessions[1]):
        states = (
            await db.scalars(
                select(Step.state).where(Step.session_id == s.id).order_by(Step.id)
            )
        ).all()
        assert states[-1] == StepState.pending  # a fresh runnable step

    # Narrowed retry: nothing eligible in the selection -> 0.
    r = await client.post(
        f"/api/jobs/{job.id}/retry", json={"session_ids": [sessions[2].id]}
    )
    assert r.json()["retried"] == 0
    assert (await client.post("/api/jobs/999/retry", json={})).status_code == 404


async def test_attention_walks_and_wraps_across_the_job(client, db):
    """"Next" must visit every session that needs the user (open gate OR
    pending proposal) and wrap around past `after` — otherwise reviewing
    a job leaves orphaned decisions behind."""
    job, sessions = await _job_with_sessions(
        db,
        [
            {"phase": SessionPhase.ocr_review, "step_state": StepState.awaiting_user},
            {"phase": SessionPhase.done},
            {"phase": SessionPhase.done},
        ],
    )
    # Session 3 needs attention through a pending proposal instead.
    db.add(Proposal(
        session_id=sessions[2].id, kind="update_document_metadata",
        agent_payload={"kind": "update_document_metadata", "document_id": 3},
        status=ProposalStatus.pending,
    ))
    await db.commit()

    r = await client.get(f"/api/jobs/{job.id}/attention")
    body = r.json()
    assert body["remaining"] == 2
    assert body["next_session_id"] == sessions[0].id
    # Continue past the gate session -> the proposal session.
    r = await client.get(f"/api/jobs/{job.id}/attention", params={"after": sessions[0].id})
    assert r.json()["next_session_id"] == sessions[2].id
    # Past the last one -> wraps to the first again.
    r = await client.get(f"/api/jobs/{job.id}/attention", params={"after": sessions[2].id})
    assert r.json()["next_session_id"] == sessions[0].id
    assert (await client.get("/api/jobs/999/attention")).status_code == 404


async def test_attention_empty_job_reports_nothing(client, db):
    job = Job(kind="bulk_analyze", total=0)
    db.add(job)
    await db.commit()
    body = (await client.get(f"/api/jobs/{job.id}/attention")).json()
    assert body == {"next_session_id": None, "remaining": 0}


async def test_next_batch_of_exhausted_corpus_is_422(client, db, respx_mock):
    """The "Analyze next batch" button on a fully-curated corpus must
    say so instead of creating an empty job."""
    from httpx import Response

    from tests.conftest import PAPERLESS_URL

    s = Session(agent_kind=AgentKind.document, entity_type=EntityType.document,
                entity_id=1, phase=SessionPhase.done)
    db.add(s)
    await db.commit()
    respx_mock.get(f"{PAPERLESS_URL}/api/documents/").mock(
        return_value=Response(200, json={
            "count": 1, "next": None,
            "results": [{"id": 1, "title": "Doc 1", "content": "", "tags": []}],
        })
    )
    r = await client.post("/api/jobs", json={"next_batch": 5})
    assert r.status_code == 422
    assert "corpus is done" in r.json()["detail"]["message"]
