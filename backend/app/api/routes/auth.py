"""Login/logout/me. The ``me`` endpoint is reachable without auth so
the login page can know the configured mode."""

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
    session_secret,
    validate_paperless_credentials,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


async def resolve_user(request: Request) -> CurrentUser | None:
    cfg = get_settings().auth
    if cfg.mode == "none":
        return CurrentUser(name="user")
    if cfg.mode == "proxy":
        name = request.headers.get(cfg.proxy_header)
        return CurrentUser(name=name) if name else None
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    return parse_cookie(cookie, await session_secret())


@router.get("/me")
async def me(request: Request) -> AuthMeOut:
    user = await resolve_user(request)
    return AuthMeOut(mode=get_settings().auth.mode, user=user.name if user else None)


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> AuthMeOut:
    cfg = get_settings().auth
    if cfg.mode != "paperless":
        raise HTTPException(409, "login is only available in paperless auth mode")
    token = await validate_paperless_credentials(body.username, body.password)
    if token is None:
        raise HTTPException(
            401,
            {"code": "bad_credentials", "message": "invalid username or password"},
        )
    user = CurrentUser(name=body.username, paperless_token=token)
    response.set_cookie(
        COOKIE_NAME,
        make_cookie(user, await session_secret()),
        max_age=cfg.session_hours * 3600,
        httponly=True,
        samesite="lax",
        path="/",
    )
    await record(db, "auth", "login", user=body.username)
    await db.commit()
    return AuthMeOut(mode=cfg.mode, user=user.name)


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
    return AuthMeOut(mode=get_settings().auth.mode, user=None)
