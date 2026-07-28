"""Login/logout/me. Credentials are validated against paperless itself
(``POST /api/token/``) — paperless is the user store; the signed cookie
carries the user's own paperless token for attributed applies."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# NB: app.api.deps defers ITS import of this module into require_user's
# body, which is what keeps this top-level import cycle-free.
from app.api.deps import require_user
from app.api.schemas import AuthMeOut, AuthSessionOut
from app.config import get_settings
from app.db.models import AuthSession, utcnow
from app.db.session import get_session
from app.services.audit import record
from app.services.auth import (
    COOKIE_NAME,
    CurrentUser,
    create_auth_session,
    make_cookie,
    parse_cookie,
    resolve_role,
    revoke_auth_session,
    session_secret,
    validate_paperless_credentials,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


async def resolve_user(request: Request) -> CurrentUser | None:
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    return parse_cookie(cookie, await session_secret())


@router.get("/me")
async def me(request: Request) -> AuthMeOut:
    user = await resolve_user(request)
    return AuthMeOut(
        user=user.name if user else None,
        role=user.role if user else "user",
    )


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> AuthMeOut:
    cfg = get_settings().auth
    token = await validate_paperless_credentials(body.username, body.password)
    if token is None:
        await record(db, "auth", "login_failed", user=body.username)
        await db.commit()
        raise HTTPException(
            401,
            {"code": "bad_credentials", "message": "invalid username or password"},
        )
    role = await resolve_role(body.username)
    # Server-side session row FIRST (AUDIT API-F8): the cookie is only
    # a pointer — revoking the row ends the session, cookie or not.
    sid = await create_auth_session(
        db,
        username=body.username,
        role=role,
        user_agent=request.headers.get("User-Agent", ""),
    )
    user = CurrentUser(
        name=body.username, paperless_token=token, role=role, sid=sid
    )
    response.set_cookie(
        COOKIE_NAME,
        make_cookie(user, await session_secret()),
        max_age=cfg.session_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=cfg.cookie_secure,
        path="/",
    )
    await record(db, "auth", "login", user=body.username, role=role)
    await db.commit()
    return AuthMeOut(user=user.name, role=user.role)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> AuthMeOut:
    user = await resolve_user(request)
    response.delete_cookie(COOKIE_NAME, path="/")
    if user is not None:
        if user.sid:
            await revoke_auth_session(db, user.sid)
        await record(db, "auth", "logout", user=user.name)
        await db.commit()
    return AuthMeOut(user=None)


# ----- login-session management (Settings → Sessions) -----------------


@router.get("/sessions")
async def list_auth_sessions(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> list[AuthSessionOut]:
    """Live login sessions: the caller's own — admins see everyone's.
    Revoked and expired sessions don't appear."""
    user = await require_user(request, db)
    q = (
        select(AuthSession)
        .where(AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow())
        .order_by(AuthSession.last_seen_at.desc())
    )
    if not user.is_admin:
        q = q.where(AuthSession.username == user.name)
    rows = (await db.scalars(q)).all()
    return [
        AuthSessionOut(
            sid=r.sid,
            username=r.username,
            role=r.role,
            user_agent=r.user_agent,
            created_at=r.created_at,
            last_seen_at=r.last_seen_at,
            expires_at=r.expires_at,
            current=r.sid == user.sid,
        )
        for r in rows
    ]


@router.delete("/sessions/{sid}")
async def revoke_session(
    sid: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> AuthMeOut:
    """End a login session server-side. The CURRENT session is refused
    (sign out instead) — a one-click self-lockout helps nobody."""
    user = await require_user(request, db)
    if sid == user.sid:
        raise HTTPException(
            409,
            {
                "code": "current_session",
                "message": "sign out instead of revoking the current session",
            },
        )
    row = await db.scalar(select(AuthSession).where(AuthSession.sid == sid))
    # Non-admins can only see/end their own; unknown-or-foreign is the
    # SAME 404 (no session enumeration, opaque sids notwithstanding).
    if row is None or (not user.is_admin and row.username != user.name):
        raise HTTPException(404, {"code": "not_found", "message": "no such session"})
    await revoke_auth_session(db, sid)
    await record(
        db, "auth", "session_revoked",
        user=user.name, target_user=row.username,
    )
    await db.commit()
    return AuthMeOut(user=user.name, role=user.role)
