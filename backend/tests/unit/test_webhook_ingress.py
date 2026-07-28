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
