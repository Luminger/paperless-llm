"""User preferences: persisted server-side for a consistent experience
across browsers. Typed — unknown keys are rejected."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_user
from app.db.models import UserPref
from app.db.session import get_session
from app.services.audit import record
from app.services.auth import CurrentUser

router = APIRouter(prefix="/api", tags=["prefs"])

DateFormat = Literal["system", "iso", "eu", "us"]
TimeFormat = Literal["24h", "24h-seconds", "12h", "12h-seconds"]
PanelSide = Literal["left", "right"]


class PrefsOut(BaseModel):
    date_format: DateFormat = "system"
    time_format: TimeFormat = "24h-seconds"
    # "system" or an IANA zone name.
    time_zone: str = "system"
    # Which side the session document panel docks on.
    doc_panel_side: PanelSide = "right"
    # Prompt tuning (empty base = the system-supplied prompt).
    agent_prompt_base: str = ""
    agent_prompt_addition: str = ""
    ocr_prompt_base: str = ""
    ocr_prompt_addition: str = ""


class PrefsUpdate(BaseModel):
    date_format: DateFormat | None = None
    time_format: TimeFormat | None = None
    time_zone: str | None = None
    doc_panel_side: PanelSide | None = None

    @field_validator("time_zone")
    @classmethod
    def _known_zone(cls, v: str | None) -> str | None:
        # AUDIT API-F18: a bad zone would crash every date render in the
        # frontend — reject it at the door.
        if v is None or v == "system":
            return v
        import zoneinfo

        if v not in zoneinfo.available_timezones():
            raise ValueError(f"unknown time zone {v!r}")
        return v
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
    body: PrefsUpdate,
    db: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> PrefsOut:
    # Display preferences are everyone's; prompt tuning shapes the
    # agent for the whole workspace and is admin-only.
    touched = body.model_dump(exclude_none=True)
    if any(k.endswith(("_base", "_addition")) for k in touched) and not user.is_admin:
        raise HTTPException(
            403,
            {"code": "forbidden", "message": "prompt tuning requires administrator rights"},
        )
    changed: dict[str, str] = {}
    for key, value in body.model_dump(exclude_none=True).items():
        row = await db.get(UserPref, key)
        if row is None:
            db.add(UserPref(key=key, value=str(value)))
        else:
            row.value = str(value)
        changed[key] = (
            str(value)
            if key.startswith(("date_", "time_", "doc_"))
            else f"({len(str(value))} chars)"
        )
    if changed:
        await record(db, "prefs", "updated", **changed)
    await db.commit()
    return await _load(db)
