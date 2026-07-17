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
