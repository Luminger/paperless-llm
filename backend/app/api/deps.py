"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, Request

from app.config import get_settings
from app.paperless import PaperlessClient
from app.services.actor import actor_var
from app.services.auth import CurrentUser


async def require_user(request: Request) -> CurrentUser:
    """Attached to every protected router. Resolves the caller from the
    signed session cookie, 401s otherwise, and attributes all work of
    this request to the named user (actor contextvar)."""
    from app.api.routes.auth import resolve_user

    user = await resolve_user(request)
    if user is None:
        raise HTTPException(
            401, {"code": "unauthorized", "message": "authentication required"}
        )
    request.state.user = user
    actor_var.set(f"user:{user.name}")
    return user


async def require_admin(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    """Settings and other system-shaping writes are for admins — i.e.
    paperless superusers (see services.auth.resolve_role)."""
    if not user.is_admin:
        raise HTTPException(
            403,
            {"code": "forbidden", "message": "administrator rights required"},
        )
    return user


async def get_paperless(request: Request) -> AsyncIterator[PaperlessClient]:
    s = get_settings().paperless
    # Paperless-mode logins act under THEIR OWN paperless token, so
    # paperless's audit trail names the real person and paperless
    # permissions apply naturally. Background work uses the app token.
    user: CurrentUser | None = getattr(request.state, "user", None)
    token = (
        user.paperless_token
        if user is not None and user.paperless_token
        else s.token
    )
    async with PaperlessClient(
        s.base_url,
        token,
        timeout=s.timeout_seconds,
        username=s.username,
        password=s.password,
        verify_tls=s.verify_tls,
    ) as client:
        yield client
