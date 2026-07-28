"""Proposal API guard paths: archived sessions, revert eligibility,
and list filtering.

An archived session is history: its proposals must never forward-apply
(only the journal reverts), and the revert endpoints must refuse cleanly
when there is nothing revertible — these 409s are what keeps the review
UI honest."""

from __future__ import annotations

import httpx
import pytest

from app.api.deps import get_paperless, require_user
from app.db.models import (
    AgentKind,
    AppliedChange,
    Proposal,
    ProposalStatus,
    Session,
    utcnow,
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


async def _seed(db, *, archived=False, status=ProposalStatus.pending) -> Proposal:
    s = Session(agent_kind=AgentKind.document,
                archived_at=utcnow() if archived else None)
    db.add(s)
    await db.flush()
    p = Proposal(
        session_id=s.id,
        kind="update_document_metadata",
        agent_payload={"kind": "update_document_metadata", "document_id": 7,
                       "title": "T"},
        status=status,
    )
    db.add(p)
    await db.commit()
    return p


async def test_archived_session_proposals_cannot_be_applied(client, db):
    p = await _seed(db, archived=True)
    r = await client.post(f"/api/proposals/{p.id}/apply")
    assert r.status_code == 409
    assert "archived" in r.json()["detail"]["message"]
    # Nothing was claimed: the proposal is still pending, not `applying`.
    await db.refresh(p)
    assert p.status == ProposalStatus.pending


async def test_revert_endpoints_refuse_when_never_applied(client, db):
    p = await _seed(db)
    assert (await client.get(f"/api/proposals/{p.id}/revert-check")).status_code == 409
    r = await client.post(f"/api/proposals/{p.id}/revert")
    assert r.status_code == 409
    assert "never applied" in r.json()["detail"]["message"]


async def test_revert_check_refuses_already_reverted_change(client, db):
    """A reverted change is spent: the UI must learn 409 (button gone),
    not get a noop-probe against paperless."""
    p = await _seed(db, status=ProposalStatus.applied)
    db.add(AppliedChange(
        proposal_id=p.id,
        paperless_before={"document": {"id": 7, "title": "old"}},
        paperless_after={"document": {"id": 7, "title": "T"}},
        reverted_at=utcnow(),
    ))
    await db.commit()
    assert (await client.get(f"/api/proposals/{p.id}/revert-check")).status_code == 409


async def test_list_filters_by_status_and_session(client, db):
    pending = await _seed(db)
    applied = await _seed(db, status=ProposalStatus.applied)
    r = await client.get("/api/proposals", params={"status": "applied"})
    body = r.json()
    assert body["count"] == 1 and body["results"][0]["id"] == applied.id
    r = await client.get("/api/proposals", params={"session_id": pending.session_id})
    body = r.json()
    assert body["count"] == 1 and body["results"][0]["id"] == pending.id
