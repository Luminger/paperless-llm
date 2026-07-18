"""Auth modes: cookie integrity, proxy header trust, enforcement."""

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


async def test_mode_none_is_open(client):
    r = await client.get("/api/auth/me")
    assert r.json() == {"mode": "none", "user": "user"}
    assert (await client.get("/api/stats")).status_code == 200


async def test_mode_proxy_trusts_header_and_401s_without(client, monkeypatch):
    get_settings().auth.mode = "proxy"
    try:
        r = await client.get("/api/stats")
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "unauthorized"
        r = await client.get("/api/stats", headers={"Remote-User": "simon"})
        assert r.status_code == 200
        me = await client.get("/api/auth/me", headers={"Remote-User": "simon"})
        assert me.json() == {"mode": "proxy", "user": "simon"}
    finally:
        get_settings().auth.mode = "none"


async def test_mode_paperless_login_flow(client, respx_mock, monkeypatch):
    from app.services import auth as auth_service

    get_settings().auth.mode = "paperless"
    monkeypatch.setattr(auth_service, "_secret_cache", "test-secret")
    respx_mock.post("http://paperless.test/api/token/").mock(
        side_effect=lambda request: (
            httpx.Response(200, json={"token": "user-token"})
            if b"simon" in request.content
            else httpx.Response(400, json={})
        )
    )
    get_settings().paperless.base_url = "http://paperless.test"
    try:
        # No cookie: 401 on protected routes, me shows the mode.
        assert (await client.get("/api/stats")).status_code == 401
        assert (await client.get("/api/auth/me")).json()["user"] is None
        # Bad credentials rejected.
        r = await client.post(
            "/api/auth/login", json={"username": "evil", "password": "x"}
        )
        assert r.status_code == 401
        # Good credentials: cookie set, protected routes open.
        r = await client.post(
            "/api/auth/login", json={"username": "simon", "password": "pw"}
        )
        assert r.status_code == 200
        assert r.json() == {"mode": "paperless", "user": "simon"}
        assert (await client.get("/api/stats")).status_code == 200
        me = await client.get("/api/auth/me")
        assert me.json()["user"] == "simon"
        # Logout clears the cookie.
        await client.post("/api/auth/logout")
        assert (await client.get("/api/stats")).status_code == 401
    finally:
        get_settings().auth.mode = "none"
