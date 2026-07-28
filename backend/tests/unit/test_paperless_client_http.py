"""PaperlessClient HTTP plumbing: pagination `next`-URL handling,
lazy username/password auth, and error tolerance.

The client is the ONLY code that talks to paperless — a token leaking
to a foreign host or a silently truncated listing here corrupts every
feature above it."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.paperless import PaperlessClient, PaperlessError
from tests.conftest import PAPERLESS_URL


def _tag(tag_id: int, name: str) -> dict:
    return {"id": tag_id, "name": name, "match": "", "matching_algorithm": 0}


# ----- pagination ------------------------------------------------------


@respx.mock
async def test_drain_relativizes_absolute_next_urls(paperless_client):
    """AUDIT API-F16: `next` is built from paperless's own Host header.
    In split-horizon setups following it verbatim would send our auth
    token to a DIFFERENT host — the client must re-relativize to its own
    base_url, keeping only path+query."""
    def respond(request):
        if request.url.params.get("page") == "2":
            return Response(200, json={"count": 2, "next": None,
                                       "results": [_tag(2, "b")]})
        return Response(200, json={
            "count": 2,
            # Foreign host, as paperless would emit behind a proxy.
            "next": "http://paperless.internal:8000/api/tags/?page=2&page_size=100",
            "results": [_tag(1, "a")],
        })

    route = respx.get(f"{PAPERLESS_URL}/api/tags/").mock(side_effect=respond)
    tags = await paperless_client.list_tags()
    assert [t.id for t in tags] == [1, 2]
    second = route.calls[-1].request
    assert second.url.host == "paperless.test"  # never paperless.internal
    assert second.url.params["page"] == "2"
    assert second.headers["Authorization"] == "Token test-token"


@respx.mock
async def test_drain_avoids_double_subpath_prefix():
    """Subpath-hosted paperless (base_url with a path): the kept path
    from `next` already carries the prefix — naively joining would
    request /paperless/paperless/api/… and 404 every page after the
    first."""
    calls: list[str] = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.params.get("page") == "2":
            return Response(200, json={"count": 2, "next": None,
                                       "results": [_tag(2, "b")]})
        return Response(200, json={
            "count": 2,
            "next": "http://paperless.test/paperless/api/tags/?page=2",
            "results": [_tag(1, "a")],
        })

    respx.get(url__regex=r"http://paperless\.test/.*").mock(side_effect=respond)
    async with PaperlessClient("http://paperless.test/paperless", "tok") as client:
        tags = await client.list_tags()
    assert [t.id for t in tags] == [1, 2]
    assert calls == ["/paperless/api/tags/", "/paperless/api/tags/"]


# ----- lazy username/password auth ------------------------------------


@respx.mock
async def test_password_auth_fetches_token_once_then_reuses_it():
    """Token-less construction (throwaway instances): the first request
    obtains a token via /api/token/ and every request carries it —
    without re-authenticating each call."""
    token_route = respx.post(f"{PAPERLESS_URL}/api/token/").mock(
        return_value=Response(200, json={"token": "fresh-token"})
    )
    doc_route = respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json={"id": 7, "title": "x", "content": "",
                                         "tags": []})
    )
    async with PaperlessClient(PAPERLESS_URL, username="admin", password="pw") as client:
        await client.get_document(7)
        await client.get_document(7)
    assert token_route.call_count == 1
    assert doc_route.calls.last.request.headers["Authorization"] == "Token fresh-token"


@respx.mock
async def test_password_auth_failure_is_a_paperless_error():
    """Bad credentials surface as PaperlessError with the HTTP status —
    not a raw httpx exception that upper layers don't catch."""
    respx.post(f"{PAPERLESS_URL}/api/token/").mock(
        return_value=Response(403, json={"detail": "nope"})
    )
    async with PaperlessClient(PAPERLESS_URL, username="admin", password="wrong") as c:
        with pytest.raises(PaperlessError, match="token fetch failed") as exc:
            await c.get_document(7)
    assert exc.value.status_code == 403


async def test_no_credentials_at_all_is_a_clean_error():
    async with PaperlessClient(PAPERLESS_URL) as client:
        with pytest.raises(PaperlessError, match="auth not configured"):
            await client.get_document(7)


# ----- error tolerance -------------------------------------------------


@respx.mock
async def test_http_error_carries_status_code(paperless_client):
    """Callers branch on status_code (e.g. 404 = already deleted in the
    apply engine) — it must survive the exception translation."""
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(404, json={"detail": "gone"})
    )
    with pytest.raises(PaperlessError) as exc:
        await paperless_client.get_document(7)
    assert exc.value.status_code == 404


@respx.mock
async def test_post_document_accepts_json_and_plain_text_task_ids(paperless_client):
    """paperless returns the consume-task uuid either as JSON or as a
    quoted text body depending on version — both must yield the bare
    uuid, which get_task() then polls."""
    route = respx.post(f"{PAPERLESS_URL}/api/documents/post_document/").mock(
        return_value=Response(200, text='"abc-123"',
                              headers={"content-type": "text/plain"})
    )
    task = await paperless_client.post_document(b"%PDF-1.4", "a.pdf", title="A")
    assert task == "abc-123"

    route.mock(return_value=Response(200, json="def-456"))
    assert await paperless_client.post_document(b"%PDF-1.4", "a.pdf") == "def-456"
