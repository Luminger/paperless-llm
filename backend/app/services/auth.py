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
import secrets
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db.models import UserPref
from app.db.session import session_scope

COOKIE_NAME = "pllm_session"
_SECRET_KEY = "_auth.session_secret"  # never surfaced by the prefs API

_secret_cache: str | None = None


@dataclass(frozen=True)
class CurrentUser:
    name: str
    # The user's own paperless token (paperless mode) — their applied
    # changes run under THEIR paperless identity.
    paperless_token: str | None = None


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
            {"u": user.name, "t": user.paperless_token, "exp": exp}
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
    return CurrentUser(name=name, paperless_token=token if isinstance(token, str) else None)


async def validate_paperless_credentials(
    username: str, password: str
) -> str | None:
    """Ask paperless itself: valid credentials yield the user's API
    token (their identity for applied changes), invalid ones None."""
    base = get_settings().paperless.base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{base}/api/token/",
            json={"username": username, "password": password},
        )
    if resp.status_code != 200:
        return None
    token = resp.json().get("token")
    return token if isinstance(token, str) and token else None
