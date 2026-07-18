"""ONE auth story: paperless credentials -> signed cookie session.
Cookie integrity, login/logout flow, enforcement on protected routes."""

from __future__ import annotations

import httpx
import pytest

from app.config import get_settings
from app.services.auth import CurrentUser, make_cookie, parse_cookie

SECRET = "test-secret"


def test_cookie_roundtrip_and_tamper_resistance():
    user = CurrentUser(name="simon", paperless_token="tok123")
    cookie = make_cookie(user, SECRET)
    parsed = parse_cookie(cookie, SECRET)
    assert parsed == user
    # Wrong secret, flipped payload byte, malformed value: all rejected.
    assert parse_cookie(cookie, "other-secret") is None
    payload, sig = cookie.rsplit(".", 1)
    assert parse_cookie(f"X{payload[1:]}.{sig}", SECRET) is None
    assert parse_cookie("garbage", SECRET) is None


@pytest.fixture
async def client(db, paperless_client, monkeypatch):
    from app.api.deps import get_paperless
    from app.db.session import get_session
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_paperless] = lambda: paperless_client
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_login_flow(client, respx_mock, monkeypatch):
    from app.services import auth as auth_service

    monkeypatch.setattr(auth_service, "_secret_cache", "test-secret")
    respx_mock.post("http://paperless.test/api/token/").mock(
        side_effect=lambda request: (
            httpx.Response(200, json={"token": "user-token"})
            if b"simon" in request.content
            else httpx.Response(400, json={})
        )
    )
    # Role lookup runs under the app's own credentials.
    respx_mock.get("http://paperless.test/api/users/").mock(
        return_value=httpx.Response(200, json={"count": 1, "next": None, "results": [
            {"id": 2, "username": "simon", "is_superuser": True},
        ]})
    )
    get_settings().paperless.base_url = "http://paperless.test"
    get_settings().paperless.token = "app-token"  # role lookup runs with this
    # No cookie: protected routes 401, me shows nobody, open routes stay open.
    r = await client.get("/api/stats")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "unauthorized"
    assert (await client.get("/api/auth/me")).json() == {"user": None, "role": "user"}
    assert (await client.get("/api/health")).status_code == 200
    # Bad credentials rejected.
    r = await client.post("/api/auth/login", json={"username": "evil", "password": "x"})
    assert r.status_code == 401
    # Good credentials: cookie set, protected routes open.
    r = await client.post("/api/auth/login", json={"username": "simon", "password": "pw"})
    assert r.status_code == 200
    assert r.json() == {"user": "simon", "role": "admin"}
    assert (await client.get("/api/stats")).status_code == 200
    assert (await client.get("/api/auth/me")).json()["user"] == "simon"
    # Logout clears the cookie.
    await client.post("/api/auth/logout")
    assert (await client.get("/api/stats")).status_code == 401


async def test_role_falls_back_to_user_on_lookup_failure(
    client, respx_mock, monkeypatch, caplog
):
    """Background token can't read /api/users/ -> everyone is a regular
    user, and the log names the fix (superuser background credentials)."""
    from app.services import auth as auth_service

    monkeypatch.setattr(auth_service, "_secret_cache", "test-secret")
    respx_mock.post("http://paperless.test/api/token/").mock(
        return_value=httpx.Response(200, json={"token": "user-token"})
    )
    respx_mock.get("http://paperless.test/api/users/").mock(
        return_value=httpx.Response(403, json={})
    )
    get_settings().paperless.base_url = "http://paperless.test"
    r = await client.post("/api/auth/login", json={"username": "simon", "password": "pw"})
    assert r.status_code == 200
    assert r.json() == {"user": "simon", "role": "user"}
    assert "cannot determine admin status" in caplog.text
