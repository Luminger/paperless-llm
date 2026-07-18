"""Login/logout/me. Credentials are validated against paperless itself
(``POST /api/token/``) — paperless is the user store; the signed cookie
carries the user's own paperless token for attributed applies."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AuthMeOut
from app.config import get_settings
from app.db.session import get_session
from app.services.audit import record
from app.services.auth import (
    COOKIE_NAME,
    CurrentUser,
    make_cookie,
    parse_cookie,
    resolve_role,
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
    user = CurrentUser(name=body.username, paperless_token=token, role=role)
    response.set_cookie(
        COOKIE_NAME,
        make_cookie(user, await session_secret()),
        max_age=cfg.session_hours * 3600,
        httponly=True,
        samesite="lax",
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
        await record(db, "auth", "logout", user=user.name)
        await db.commit()
    return AuthMeOut(user=None)
