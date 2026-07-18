"""Token-vs-credentials auth behavior of PaperlessClient."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.paperless import PaperlessClient, PaperlessError

URL = "http://paperless.test"


@respx.mock
async def test_username_password_fetches_token_once():
    token_route = respx.post(f"{URL}/api/token/").mock(
        return_value=Response(200, json={"token": "fetched-token"})
    )
    docs_route = respx.get(f"{URL}/api/documents/").mock(
        return_value=Response(
            200, json={"count": 0, "next": None, "previous": None, "results": []}
        )
    )
    async with PaperlessClient(URL, username="admin", password="admin") as c:
        await c.search_documents(query="x")
        await c.search_documents(query="y")

    assert token_route.call_count == 1  # cached after first fetch
    assert (
        docs_route.calls.last.request.headers["Authorization"] == "Token fetched-token"
    )


@respx.mock
async def test_explicit_token_skips_fetch():
    token_route = respx.post(f"{URL}/api/token/")
    respx.get(f"{URL}/api/documents/").mock(
        return_value=Response(
            200, json={"count": 0, "next": None, "previous": None, "results": []}
        )
    )
    async with PaperlessClient(URL, "explicit-token") as c:
        await c.search_documents(query="x")
    assert not token_route.called


async def test_no_auth_configured_raises():
    async with PaperlessClient(URL) as c:
        with pytest.raises(PaperlessError, match="auth not configured"):
            await c.search_documents(query="x")


@respx.mock
async def test_bad_credentials_raise():
    respx.post(f"{URL}/api/token/").mock(
        return_value=Response(400, json={"non_field_errors": ["bad creds"]})
    )
    async with PaperlessClient(URL, username="admin", password="wrong") as c:
        with pytest.raises(PaperlessError, match="token fetch failed: 400"):
            await c.search_documents(query="x")

@respx.mock
async def test_drain_follows_next_on_subpath_hosted_paperless():
    """Reinspection of AUDIT API-F16: the token-leak fix re-relativizes
    `next` URLs — for a paperless hosted under a URL subpath the kept
    path must not end up double-prefixed by httpx's base_url merge."""
    base = "http://host.test/paperless"
    route = respx.get(f"{base}/api/tags/").mock(side_effect=[
        Response(200, json={
            "count": 101,
            # paperless emits an ABSOLUTE next incl. its subpath
            "next": f"{base}/api/tags/?page=2&page_size=100",
            "results": [
                {"id": i, "name": f"t{i}", "document_count": 0}
                for i in range(1, 101)
            ],
        }),
        Response(200, json={
            "count": 101, "next": None,
            "results": [{"id": 101, "name": "t101", "document_count": 0}],
        }),
    ])
    async with PaperlessClient(base, "tok") as c:
        tags = await c.list_tags()
    assert len(tags) == 101
    assert route.call_count == 2
    second = route.calls[1].request.url
    # the double-prefix bug would have requested /paperless/paperless/api/tags/
    assert second.path == "/paperless/api/tags/"
    # and the empty-params-dict bug stripped ?page=2, refetching page 1 forever
    assert second.params["page"] == "2"


@respx.mock
async def test_drain_never_follows_next_to_a_foreign_host():
    """AUDIT API-F16 proper: split-horizon `next` must be re-anchored to
    OUR base_url — the token never travels to the foreign host."""
    respx.get("http://paperless.test/api/tags/").mock(side_effect=[
        Response(200, json={
            "count": 2,
            "next": "http://internal.other/api/tags/?page=2",
            "results": [{"id": 1, "name": "a", "document_count": 0}],
        }),
        Response(200, json={
            "count": 2, "next": None,
            "results": [{"id": 2, "name": "b", "document_count": 0}],
        }),
    ])
    foreign = respx.get("http://internal.other/api/tags/")
    async with PaperlessClient(URL, "tok") as c:
        tags = await c.list_tags()
    assert len(tags) == 2
    assert not foreign.called
