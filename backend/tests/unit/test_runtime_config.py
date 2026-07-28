"""Runtime config overrides: the Settings UI's persistence layer.

The invariants: overrides survive a restart (init_from_db), corrupted
storage degrades to "no overrides" instead of crashing startup, and the
env layer stays authoritative over UI values."""

from __future__ import annotations

from sqlalchemy import select

from app.config import (
    get_settings,
    reset_settings_cache,
    runtime_overrides,
    set_runtime_overrides,
)
from app.db.models import UserPref
from app.services.runtime_config import _KEY, init_from_db, load_overrides, save_overrides


async def test_save_load_roundtrip_uses_a_single_row(db):
    await save_overrides(db, {"llm.agent.model": "m1"})
    await save_overrides(db, {"llm.agent.model": "m2", "llm.ocr.render_dpi": 200})
    await db.commit()
    assert await load_overrides(db) == {
        "llm.agent.model": "m2",
        "llm.ocr.render_dpi": 200,
    }
    rows = (await db.scalars(select(UserPref))).all()
    assert len(rows) == 1  # replaced, not appended


async def test_missing_row_means_no_overrides(db):
    assert await load_overrides(db) == {}


async def test_corrupted_storage_degrades_to_empty(db):
    """A truncated/garbled blob must not brick startup — the app comes
    up with file/env config and the user re-saves in the UI."""
    db.add(UserPref(key=_KEY, value="{not json"))
    await db.commit()
    assert await load_overrides(db) == {}


async def test_non_dict_payload_is_ignored(db):
    db.add(UserPref(key=_KEY, value='["a", "list"]'))
    await db.commit()
    assert await load_overrides(db) == {}


async def test_init_from_db_applies_persisted_overrides(tmp_path, monkeypatch):
    """Restart semantics: what the UI saved becomes the active settings
    layer again — visible through get_settings(), not just the raw dict."""
    monkeypatch.setenv("PLLM_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/s.sqlite3")
    reset_settings_cache()
    from app.db.session import dispose_engine, init_db, session_scope

    await dispose_engine()
    await init_db()
    try:
        async with session_scope() as dbs:
            await save_overrides(dbs, {"llm.agent.model": "from-the-db"})
            await dbs.commit()
        await init_from_db()
        assert runtime_overrides() == {"llm.agent.model": "from-the-db"}
        assert get_settings().llm.agent.model == "from-the-db"
    finally:
        set_runtime_overrides({})
        await dispose_engine()
        reset_settings_cache()


async def test_env_beats_ui_override(monkeypatch):
    """Precedence contract: env > UI override. An operator pinning a
    value via environment must never be silently outvoted by a stale DB
    override."""
    monkeypatch.setenv("PLLM_LLM__AGENT__MODEL", "from-env")
    set_runtime_overrides({"llm.agent.model": "from-ui"})
    try:
        assert get_settings().llm.agent.model == "from-env"
    finally:
        set_runtime_overrides({})
        monkeypatch.delenv("PLLM_LLM__AGENT__MODEL")
        reset_settings_cache()


async def test_ui_override_beats_defaults(monkeypatch):
    monkeypatch.delenv("PLLM_LLM__AGENT__MODEL", raising=False)
    set_runtime_overrides({"llm.agent.model": "from-ui"})
    try:
        assert get_settings().llm.agent.model == "from-ui"
    finally:
        set_runtime_overrides({})
        reset_settings_cache()
