"""Server-persisted user preferences (see routes/prefs.py for the API).
Also consumed by the agent runner: the model writes dates/times the way
the user has chosen to see them."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserPref

DEFAULTS = {
    "date_format": "system",
    "time_format": "24h-seconds",
    "time_zone": "system",
    # Prompt tuning: base overrides replace the system-supplied base
    # prompt (empty = use the built-in); additions are appended. Both
    # exist because small local models often need per-setup tuning.
    "agent_prompt_base": "",
    "agent_prompt_addition": "",
    "ocr_prompt_base": "",
    "ocr_prompt_addition": "",
}


async def get_prefs(db: AsyncSession) -> dict[str, str]:
    rows = (await db.scalars(select(UserPref))).all()
    stored = {r.key: r.value for r in rows if r.key in DEFAULTS}
    return {**DEFAULTS, **stored}


_DATE_EXAMPLES = {
    "iso": "2026-07-17",
    "eu": "17.07.2026",
    "us": "07/17/2026",
}


def format_instructions(prefs: dict[str, str]) -> str:
    """Tell the model how the user reads dates/times, so its prose
    matches what the UI shows. Data fields (e.g. `created`) stay ISO."""
    date = prefs.get("date_format", "system")
    time = prefs.get("time_format", "24h-seconds")
    zone = prefs.get("time_zone", "system")
    date_part = (
        f"dates as {_DATE_EXAMPLES[date]}"
        if date in _DATE_EXAMPLES
        else "dates in a natural, unambiguous form (e.g. 17 July 2026)"
    )
    time_part = "12-hour clock" if time.startswith("12h") else "24-hour clock"
    zone_part = f" (timezone {zone})" if zone != "system" else ""
    return (
        f"When writing dates or times in prose, use the user's display "
        f"preference: {date_part}, {time_part}{zone_part}. Machine fields "
        f"in proposals (e.g. `created`) always stay ISO YYYY-MM-DD."
    )


def with_owner_addition(prompt: str, addition: str) -> str:
    """Append the archive owner's standing instructions — ONE wording
    for every prompt (agent + OCR); divergent copies would change cache
    fingerprints and model behavior independently."""
    if addition.strip():
        prompt += (
            "\nAdditional instructions from the archive's owner "
            "(follow them):\n" + addition.strip() + "\n"
        )
    return prompt
