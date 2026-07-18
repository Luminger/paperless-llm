"""User preferences: persisted server-side for a consistent experience
across browsers. Typed — unknown keys are rejected."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserPref
from app.db.session import get_session
from app.services.audit import record

router = APIRouter(prefix="/api", tags=["prefs"])

DateFormat = Literal["system", "iso", "eu", "us"]
TimeFormat = Literal["24h", "24h-seconds", "12h", "12h-seconds"]


class PrefsOut(BaseModel):
    date_format: DateFormat = "system"
    time_format: TimeFormat = "24h-seconds"
    # "system" or an IANA zone name.
    time_zone: str = "system"
    # Prompt tuning (empty base = the system-supplied prompt).
    agent_prompt_base: str = ""
    agent_prompt_addition: str = ""
    ocr_prompt_base: str = ""
    ocr_prompt_addition: str = ""


class PrefsUpdate(BaseModel):
    date_format: DateFormat | None = None
    time_format: TimeFormat | None = None
    time_zone: str | None = None
    agent_prompt_base: str | None = None
    agent_prompt_addition: str | None = None
    ocr_prompt_base: str | None = None
    ocr_prompt_addition: str | None = None


async def _load(db: AsyncSession) -> PrefsOut:
    from app.services.prefs import get_prefs

    return PrefsOut(**await get_prefs(db))


@router.get("/prefs")
async def get_prefs(db: AsyncSession = Depends(get_session)) -> PrefsOut:
    return await _load(db)


@router.put("/prefs")
async def put_prefs(
    body: PrefsUpdate, db: AsyncSession = Depends(get_session)
) -> PrefsOut:
    changed: dict[str, str] = {}
    for key, value in body.model_dump(exclude_none=True).items():
        row = await db.get(UserPref, key)
        if row is None:
            db.add(UserPref(key=key, value=str(value)))
        else:
            row.value = str(value)
        changed[key] = (
            str(value) if key.startswith(("date_", "time_")) else f"({len(str(value))} chars)"
        )
    if changed:
        await record(db, "prefs", "updated", **changed)
    await db.commit()
    return await _load(db)
