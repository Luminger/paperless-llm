"""Authentication (see DESIGN.md "Authentication"): three modes, one
signed httpOnly session cookie, no user store of our own.

The cookie payload is ``base64url(json) . hmac`` — signed, not
encrypted: it carries the user's OWN paperless token (paperless mode),
which is no secret from its owner. The HMAC secret is configured or
generated once and persisted in the DB, so sessions survive restarts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db.models import UserPref
from app.db.session import session_scope

log = logging.getLogger(__name__)

COOKIE_NAME = "pllm_session"
_SECRET_KEY = "_auth.session_secret"  # never surfaced by the prefs API

_secret_cache: str | None = None


@dataclass(frozen=True)
class CurrentUser:
    name: str
    # The user's own paperless token — their applied changes run under
    # THEIR paperless identity.
    paperless_token: str | None = None
    # "admin" | "user". Derived from paperless at login time: whoever
    # holds superuser rights THERE administers this app. Carried in the
    # signed cookie for the session's lifetime.
    role: str = "user"
    # Opaque id of the login-session row backing this cookie — the
    # server-side half of the session (AUDIT API-F8): no live row, no
    # access, however valid the signature.
    sid: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


async def session_secret() -> str:
    """Configured secret, or one generated once and persisted."""
    global _secret_cache
    cfg = get_settings().auth
    if cfg.session_secret:
        return cfg.session_secret
    if _secret_cache:
        return _secret_cache
    async with session_scope() as db:
        row = await db.scalar(select(UserPref).where(UserPref.key == _SECRET_KEY))
        if row is None:
            row = UserPref(key=_SECRET_KEY, value=secrets.token_urlsafe(32))
            db.add(row)
            await db.commit()
        _secret_cache = row.value
    return _secret_cache


def _sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def make_cookie(user: CurrentUser, secret: str) -> str:
    exp = int(time.time()) + get_settings().auth.session_hours * 3600
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "u": user.name,
                "t": user.paperless_token,
                "r": user.role,
                "s": user.sid,
                "exp": exp,
            }
        ).encode()
    ).decode()
    return f"{payload}.{_sign(payload.encode(), secret)}"


def parse_cookie(value: str, secret: str) -> CurrentUser | None:
    try:
        payload, sig = value.rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(payload.encode(), secret)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("exp", 0) < time.time():
        return None
    name = data.get("u")
    if not isinstance(name, str) or not name:
        return None
    token = data.get("t")
    role = data.get("r")
    sid = data.get("s")
    return CurrentUser(
        name=name,
        paperless_token=token if isinstance(token, str) else None,
        role="admin" if role == "admin" else "user",
        sid=sid if isinstance(sid, str) and sid else None,
    )


# ----- server-side login sessions (AUDIT API-F8, second half) ---------
#
# The cookie's signature proves WHO minted it; the sid row proves it is
# still WELCOME. Validity is cached in-process with a short TTL so the
# hot path stays off the DB; revocation happens in this same process
# (single-process design, see recover()/claim), so evicting the cache
# entry makes it take effect instantly.

_SESSION_CACHE_TTL = 60.0
_session_cache: dict[str, float] = {}  # sid -> cache-entry expiry (valid sids only)


async def create_auth_session(
    db, *, username: str, role: str, user_agent: str
) -> str:
    from app.db.models import AuthSession, utcnow

    sid = secrets.token_urlsafe(24)
    hours = get_settings().auth.session_hours
    now = utcnow()
    db.add(
        AuthSession(
            sid=sid,
            username=username,
            role=role,
            user_agent=user_agent[:300],
            expires_at=now + timedelta(hours=hours),
        )
    )
    # Opportunistic hygiene: drop rows that have been dead for a month.
    from sqlalchemy import delete, or_

    await db.execute(
        delete(AuthSession).where(
            or_(
                AuthSession.expires_at < now - timedelta(days=30),
                AuthSession.revoked_at < now - timedelta(days=30),
            )
        )
    )
    return sid


async def session_alive(db, sid: str | None) -> bool:
    """Is this login session still welcome? Cached for TTL seconds;
    refresh also slides ``last_seen_at``. Runs on the caller's request
    session (guards execute before handlers — nothing to clobber)."""
    if not sid:
        return False
    now = time.monotonic()
    if _session_cache.get(sid, 0) > now:
        return True
    from app.db.models import AuthSession, utcnow

    # SQL-side expiry comparison — SQLite hands back naive datetimes.
    row = await db.scalar(
        select(AuthSession).where(
            AuthSession.sid == sid,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > utcnow(),
        )
    )
    if row is None:
        return False
    row.last_seen_at = utcnow()
    await db.commit()
    _session_cache[sid] = now + _SESSION_CACHE_TTL
    return True


async def revoke_auth_session(db, sid: str) -> bool:
    """Guarded revoke; True when THIS call flipped the row."""
    from sqlalchemy import update

    from app.db.models import AuthSession, utcnow

    res = await db.execute(
        update(AuthSession)
        .where(AuthSession.sid == sid, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    _session_cache.pop(sid, None)  # instant effect, no TTL grace
    return bool(res.rowcount)


async def validate_paperless_credentials(
    username: str, password: str
) -> str | None:
    """Ask paperless itself: valid credentials yield the user's API
    token (their identity for applied changes), invalid ones None."""
    cfg = get_settings().paperless
    base = cfg.base_url.rstrip("/")
    # Same shape as PaperlessClient._ensure_auth (form-encoded, follow
    # redirects) — the proven path against real paperless instances.
    # AUDIT API-F4: honor verify_tls/timeout_seconds — otherwise login
    # is the ONE call that fails on self-signed setups, surfacing as
    # "invalid username or password".
    async with httpx.AsyncClient(
        timeout=cfg.timeout_seconds,
        follow_redirects=True,
        verify=cfg.verify_tls,
    ) as client:
        try:
            resp = await client.post(
                f"{base}/api/token/",
                data={"username": username, "password": password},
            )
        except httpx.HTTPError as e:
            log.warning("paperless token fetch failed: %r", e)
            return None
    if resp.status_code != 200:
        return None
    token = resp.json().get("token")
    return token if isinstance(token, str) and token else None


async def resolve_role(username: str) -> str:
    """Ask paperless (via the app's background credentials) whether this
    user holds superuser rights — permissions decide, not the account
    name. A plain user cannot query their own permissions (/api/users/
    and /api/ui_settings/ 403 without extra grants — verified against a
    live instance), so the lookup runs under the app's own token; if
    THAT lacks the rights, everyone is a regular user and the log says
    why."""
    from app.paperless import make_client

    try:
        async with make_client() as client:
            data = await client._get_json(  # noqa: SLF001 — same package, thin proxy
                "/api/users/", username__iexact=username, page_size=2
            )
    except Exception as e:  # noqa: BLE001 — degrade to non-admin, loudly
        log.warning(
            "cannot determine admin status for %r via paperless "
            "(/api/users/ failed: %r) — treating as regular user. The "
            "app's background credentials must belong to a paperless "
            "superuser for role resolution to work.",
            username, e,
        )
        return "user"
    matches = [
        u for u in data.get("results", [])
        if str(u.get("username", "")).lower() == username.lower()
    ]
    if not matches:
        log.warning("paperless knows no user %r during role resolution", username)
        return "user"
    return "admin" if matches[0].get("is_superuser") else "user"
