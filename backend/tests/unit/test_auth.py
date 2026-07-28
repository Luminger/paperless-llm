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

def _mock_login(respx_mock, users=("simon", "erika")):
    respx_mock.post("http://paperless.test/api/token/").mock(
        return_value=httpx.Response(200, json={"token": "user-token"})
    )
    respx_mock.get("http://paperless.test/api/users/").mock(
        side_effect=lambda request: httpx.Response(200, json={
            "count": 1, "next": None, "results": [{
                "id": 2,
                "username": (request.url.params.get("username__iexact") or ""),
                # simon is the admin in these tests
                "is_superuser": (request.url.params.get("username__iexact") == "simon"),
            }],
        })
    )
    get_settings().paperless.base_url = "http://paperless.test"
    get_settings().paperless.token = "app-token"


async def _login(client, username):
    r = await client.post(
        "/api/auth/login", json={"username": username, "password": "pw"}
    )
    assert r.status_code == 200
    return r


async def test_session_revocation_kills_access(client, respx_mock, monkeypatch):
    """AUDIT API-F8 (second half): a revoked session dies server-side,
    valid cookie signature notwithstanding — and instantly (no TTL
    grace: the cache entry is evicted on revoke)."""
    from app.services import auth as auth_service

    monkeypatch.setattr(auth_service, "_secret_cache", "test-secret")
    monkeypatch.setattr(auth_service, "_session_cache", {})
    _mock_login(respx_mock)

    await _login(client, "simon")
    victim_cookie = dict(client.cookies)
    # second login = second session (fresh cookie jar entry replaces ours,
    # so capture the sid list via the API)
    await _login(client, "simon")
    sessions = (await client.get("/api/auth/sessions")).json()
    assert len(sessions) == 2
    current = next(s for s in sessions if s["current"])
    other = next(s for s in sessions if not s["current"])

    # Current session refuses revocation — sign out instead.
    r = await client.delete(f"/api/auth/sessions/{current['sid']}")
    assert r.status_code == 409
    # The OTHER session revokes fine…
    assert (await client.delete(f"/api/auth/sessions/{other['sid']}")).status_code == 200
    # …and its cookie is dead immediately.
    r = await client.get("/api/stats", cookies=victim_cookie)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "session_revoked"
    # The current session still works.
    assert (await client.get("/api/stats")).status_code == 200


async def test_session_listing_scoped_to_user_unless_admin(
    client, respx_mock, monkeypatch
):
    from app.services import auth as auth_service

    monkeypatch.setattr(auth_service, "_secret_cache", "test-secret")
    monkeypatch.setattr(auth_service, "_session_cache", {})
    _mock_login(respx_mock)

    await _login(client, "simon")   # admin
    admin_cookie = dict(client.cookies)
    await _login(client, "erika")   # plain user

    # erika sees only her own session…
    mine = (await client.get("/api/auth/sessions")).json()
    assert {s["username"] for s in mine} == {"erika"}
    # …and cannot revoke simon's (404, not 403 — no enumeration).
    admin_sessions = (
        await client.get("/api/auth/sessions", cookies=admin_cookie)
    ).json()
    simons = next(s for s in admin_sessions if s["username"] == "simon")
    assert (await client.delete(f"/api/auth/sessions/{simons['sid']}")).status_code == 404
    # The admin sees both users' sessions.
    assert {s["username"] for s in admin_sessions} == {"simon", "erika"}


async def test_legacy_cookie_without_sid_forces_relogin(
    client, respx_mock, monkeypatch
):
    """Cookies minted before the session registry carry no sid — they
    fail the liveness check once and force a clean re-login."""
    from app.services import auth as auth_service
    from app.services.auth import COOKIE_NAME, CurrentUser, make_cookie

    monkeypatch.setattr(auth_service, "_secret_cache", "test-secret")
    monkeypatch.setattr(auth_service, "_session_cache", {})
    legacy = make_cookie(
        CurrentUser(name="simon", role="admin", sid=None), "test-secret"
    )
    r = await client.get("/api/stats", cookies={COOKIE_NAME: legacy})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "session_revoked"


# ----- login throttling ------------------------------------------------


@pytest.fixture
def throttle(monkeypatch):
    """Isolated throttle state and a controllable clock."""
    from app.services import login_throttle

    clock = {"now": 1000.0}
    monkeypatch.setattr(login_throttle, "_entries", {})
    monkeypatch.setattr(login_throttle, "_monotonic", lambda: clock["now"])
    return clock


def _mock_bad_credentials(respx_mock):
    respx_mock.post("http://paperless.test/api/token/").mock(
        return_value=httpx.Response(400, json={})
    )
    get_settings().paperless.base_url = "http://paperless.test"


async def _fail_login(client, username="simon"):
    return await client.post(
        "/api/auth/login", json={"username": username, "password": "wrong"}
    )


async def test_login_burst_triggers_429_with_retry_after(
    client, db, respx_mock, throttle
):
    """After the free failure budget, further attempts are 429 with a
    Retry-After header — and never reach paperless (no oracle)."""
    _mock_bad_credentials(respx_mock)
    for _ in range(get_settings().auth.login_backoff_after):
        assert (await _fail_login(client)).status_code == 401
    r = await _fail_login(client)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    assert r.json()["detail"]["code"] == "too_many_attempts"
    # Throttled attempts are audit-recorded distinctly.
    from sqlalchemy import select

    from app.db.models import AuditLog
    actions = [a.action for a in (await db.scalars(select(AuditLog))).all()]
    assert actions.count("login_throttled") == 1
    assert actions.count("login_failed") == 5


async def test_login_throttle_scoped_to_username_and_ip(
    client, respx_mock, throttle
):
    """simon being throttled must not affect erika (same IP), and the
    same username from another IP keeps its own counter."""
    from app.services import login_throttle

    _mock_bad_credentials(respx_mock)
    for _ in range(get_settings().auth.login_backoff_after):
        await _fail_login(client, "simon")
    assert (await _fail_login(client, "simon")).status_code == 429
    assert (await _fail_login(client, "erika")).status_code == 401
    # Different IP, same username: unaffected (module-level — the ASGI
    # test transport always presents one peer address).
    assert login_throttle.retry_after("simon", "10.9.8.7") == 0


async def test_login_throttle_success_resets(client, respx_mock, throttle, monkeypatch):
    """A real login wipes the failure slate for that (user, ip) key."""
    from app.services import auth as auth_service

    monkeypatch.setattr(auth_service, "_secret_cache", "test-secret")
    _mock_bad_credentials(respx_mock)
    for _ in range(get_settings().auth.login_backoff_after):
        await _fail_login(client)
    assert (await _fail_login(client)).status_code == 429
    # Wait out the block, then succeed.
    throttle["now"] += 10
    respx_mock.post("http://paperless.test/api/token/").mock(
        side_effect=lambda request: (
            httpx.Response(200, json={"token": "user-token"})
            if b"right" in request.content
            else httpx.Response(400, json={})
        )
    )
    respx_mock.get("http://paperless.test/api/users/").mock(
        return_value=httpx.Response(403, json={})
    )
    r = await client.post(
        "/api/auth/login", json={"username": "simon", "password": "right"}
    )
    assert r.status_code == 200
    # Counters cleared: the next failure is #1 again, not #6.
    assert (await _fail_login(client)).status_code == 401


async def test_login_throttle_window_expiry_reallows(client, respx_mock, throttle):
    """Blocked attempts re-allow once the wait passes, and a long-idle
    key is forgiven entirely."""
    _mock_bad_credentials(respx_mock)
    for _ in range(get_settings().auth.login_backoff_after):
        await _fail_login(client)
    assert (await _fail_login(client)).status_code == 429
    # Past the current wait: attempts flow again (still to paperless).
    throttle["now"] += 5
    assert (await _fail_login(client)).status_code == 401
    # That failure doubled the wait…
    assert (await _fail_login(client)).status_code == 429
    # …but one idle cap-length forgives the whole history.
    throttle["now"] += get_settings().auth.login_backoff_cap_seconds + 1
    assert (await _fail_login(client)).status_code == 401


def test_client_ip_ignores_forwarded_header_unless_trusted(monkeypatch):
    """X-Forwarded-For is attacker-controlled without a proxy — only
    the explicit trusted-proxy flag makes the throttle honor it."""
    from starlette.requests import Request as StarletteRequest

    from app.api.routes.auth import client_ip

    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"1.2.3.4, 10.0.0.1")],
        "client": ("9.9.9.9", 1234),
    }
    request = StarletteRequest(scope)
    monkeypatch.setattr(get_settings().auth, "trust_proxy_headers", False)
    assert client_ip(request) == "9.9.9.9"
    monkeypatch.setattr(get_settings().auth, "trust_proxy_headers", True)
    assert client_ip(request) == "1.2.3.4"


async def test_session_timestamps_carry_utc_offset(client, respx_mock, monkeypatch):
    """Wire contract: every timestamp is explicit UTC. SQLite returns
    naive datetimes — bare `datetime` fields serialized them without
    the offset and browsers misread them as LOCAL time (the '2 h ago
    right after signing in' bug)."""
    from app.services import auth as auth_service

    monkeypatch.setattr(auth_service, "_secret_cache", "test-secret")
    monkeypatch.setattr(auth_service, "_session_cache", {})
    _mock_login(respx_mock)
    await _login(client, "simon")
    (s,) = (await client.get("/api/auth/sessions")).json()
    for key in ("created_at", "last_seen_at", "expires_at"):
        assert s[key].endswith("+00:00") or s[key].endswith("Z"), (key, s[key])
