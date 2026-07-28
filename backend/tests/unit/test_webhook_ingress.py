"""Webhook ingress hardening: payload extraction shapes and the
malformed-input paths (AUDIT API-F11).

Webhook bodies are user-templated in paperless workflows — every
tolerated shape here is one less support case; every malformed input
must map to a 4xx, never a 500."""

from __future__ import annotations

import httpx
import pytest

from app.api.deps import get_paperless
from app.api.routes.webhooks import _extract_document_ids
from app.config import reset_settings_cache
from app.db.session import get_session
from app.main import create_app


def test_extract_accepts_the_documented_shapes():
    assert _extract_document_ids({"document_id": 5}) == [5]
    assert _extract_document_ids({"id": "12"}) == [12]  # templated as string
    assert _extract_document_ids({"doc_id": 3}) == [3]
    assert _extract_document_ids({"documents": [1, "2", "x", None]}) == [1, 2]
    assert _extract_document_ids({"url": "https://paperless/documents/123/"}) == [123]
    assert _extract_document_ids({"doc_url": "https://p/documents/9/preview"}) == [9]


def test_extract_rejects_unicode_digits_and_garbage():
    """AUDIT API-F11: str.isdigit() is True for unicode digits like "²"
    where int() raises — those must be ignored, not crash the endpoint."""
    assert _extract_document_ids({"document_id": "²"}) == []
    assert _extract_document_ids({"documents": ["²", "٣"]}) == []
    assert _extract_document_ids({"document_id": "-5"}) == []
    assert _extract_document_ids({"url": "https://paperless/tags/3/"}) == []
    assert _extract_document_ids("just a string") == []
    assert _extract_document_ids(None) == []
    assert _extract_document_ids({}) == []


@pytest.fixture
async def client(db, paperless_client, monkeypatch):
    monkeypatch.setenv("PLLM_WEBHOOK__SECRET", "s3cret")
    reset_settings_cache()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_paperless] = lambda: paperless_client
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    reset_settings_cache()


async def test_non_ascii_token_is_403_not_500(client):
    """Reinspection regression: hmac.compare_digest on STR raises
    TypeError for non-ASCII — a garbage header must be a clean 403."""
    r = await client.post(
        "/api/webhooks/paperless",
        json={"document_id": 5},
        # Bytes: httpx refuses non-ASCII str values, but the wire (and
        # starlette) happily deliver them.
        headers={"X-PLLM-Token": "s3cret²".encode("latin-1")},
    )
    assert r.status_code == 403


async def test_non_json_body_is_422_not_500(client):
    """A workflow misconfigured to post form data or garbage must get a
    diagnosable 422 (no document id), not an internal error."""
    r = await client.post(
        "/api/webhooks/paperless",
        content=b"not json at all",
        headers={"X-PLLM-Token": "s3cret", "content-type": "text/plain"},
    )
    assert r.status_code == 422
    assert "no document id" in r.json()["detail"]["message"]


# ----- ingress caps (hardening review) ---------------------------------


HEADERS = {"X-PLLM-Token": "s3cret"}


@pytest.fixture
def dedup(monkeypatch):
    """Isolated recently-seen map and a controllable clock."""
    from app.api.routes import webhooks

    clock = {"now": 1000.0}
    monkeypatch.setattr(webhooks, "_recent", {})
    monkeypatch.setattr(webhooks, "_monotonic", lambda: clock["now"])
    return clock


async def test_oversized_body_is_413(client, dedup):
    """Both refusal paths: the declared Content-Length, and the actual
    bytes for a lying/absent header — no free multi-megabyte upload."""
    from app.config import get_settings

    limit = get_settings().webhook.max_body_bytes
    big = b'{"document_id": 5, "pad": "' + b"x" * limit + b'"}'
    r = await client.post("/api/webhooks/paperless", content=big, headers=HEADERS)
    assert r.status_code == 413
    # Lying Content-Length: the streaming cap still catches the body.
    r = await client.post(
        "/api/webhooks/paperless",
        content=big,
        headers={**HEADERS, "Content-Length": "10"},
    )
    assert r.status_code == 413


async def test_id_count_cap_is_422_and_audited(client, db, dedup):
    """Paperless posts one document per workflow event — an id flood is
    misuse and must not fan out into thousands of sessions."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import AuditLog, Session

    limit = get_settings().webhook.max_documents
    r = await client.post(
        "/api/webhooks/paperless",
        json={"documents": list(range(1, limit + 2))},
        headers=HEADERS,
    )
    assert r.status_code == 422
    assert "too many document ids" in r.json()["detail"]["message"]
    assert (await db.scalars(select(Session))).all() == []
    (entry,) = (
        await db.scalars(select(AuditLog).where(AuditLog.action == "rejected"))
    ).all()
    assert entry.detail["reason"] == "too_many_documents"


async def _finish_sessions(db):
    """Mark every session terminal — the scenario dedup exists for is a
    replay AFTER the previous run completed (skip_active in create_job
    already brakes concurrent duplicates and must not mask dedup)."""
    from sqlalchemy import select

    from app.db.models import Session, SessionPhase

    for s in (await db.scalars(select(Session))).all():
        s.phase = SessionPhase.done
    await db.commit()


async def test_duplicate_within_window_not_reenqueued(client, db, dedup):
    """A replayed id inside the dedup window is acknowledged honestly
    (200 "duplicate") but creates no new session; past the window it is
    analyzed again."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import AuditLog, Session

    r = await client.post(
        "/api/webhooks/paperless", json={"document_id": 5}, headers=HEADERS
    )
    assert r.status_code == 202
    assert r.json()["queued_documents"] == [5]
    await _finish_sessions(db)
    # Replay inside the window: no new session, honest response.
    r = await client.post(
        "/api/webhooks/paperless", json={"document_id": 5}, headers=HEADERS
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "duplicate"
    assert body["duplicate_documents"] == [5]
    assert body["queued_documents"] == [] and body["job_id"] is None
    assert len((await db.scalars(select(Session))).all()) == 1
    (skip,) = (
        await db.scalars(select(AuditLog).where(AuditLog.action == "deduplicated"))
    ).all()
    assert skip.detail["documents"] == [5]
    # Past the window the same id is fresh again.
    await _finish_sessions(db)
    dedup["now"] += get_settings().webhook.dedup_window_seconds + 1
    r = await client.post(
        "/api/webhooks/paperless", json={"document_id": 5}, headers=HEADERS
    )
    assert r.status_code == 202
    assert len((await db.scalars(select(Session))).all()) == 2


async def test_dedup_zero_window_disables(client, db, dedup, monkeypatch):
    from sqlalchemy import select

    from app.config import get_settings
    from app.db.models import Session

    monkeypatch.setattr(get_settings().webhook, "dedup_window_seconds", 0)
    for _ in range(2):
        r = await client.post(
            "/api/webhooks/paperless", json={"document_id": 5}, headers=HEADERS
        )
        assert r.status_code == 202
        await _finish_sessions(db)
    assert len((await db.scalars(select(Session))).all()) == 2


async def test_caps_do_not_bypass_the_secret(client, dedup):
    """The shared-secret gate stays first: an unauthenticated caller
    gets 403 even for oversized or flooding requests."""
    r = await client.post(
        "/api/webhooks/paperless", content=b"x" * 100_000, headers={}
    )
    assert r.status_code == 403
    r = await client.post(
        "/api/webhooks/paperless",
        json={"documents": list(range(1000))},
        headers={"X-PLLM-Token": "wrong"},
    )
    assert r.status_code == 403
