"""The migration chain IS the schema: prod upgrades via alembic while
tests create_all from the models — this test pins the two together so
they can never drift (missing index, stale enum, forgotten column)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from app.config import reset_settings_cache
from app.db.migrations import _run
from app.db.models import Base

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def _upgraded_engine(tmp_path: Path):
    url = f"sqlite:///{tmp_path}/migrated.db"
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return create_engine(url)


def _relocated_root(tmp_path: Path) -> Path:
    """The alembic tree copied OUT of the source layout — what a nix
    package installs (``PLLM_ROOT_DIR`` pointing at a packaged root)."""
    root = tmp_path / "root"
    root.mkdir()
    shutil.copy2(BACKEND_ROOT / "alembic.ini", root / "alembic.ini")
    shutil.copytree(BACKEND_ROOT / "alembic", root / "alembic")
    return root


async def test_run_migrations_uses_root_dir(monkeypatch, tmp_path):
    """The migration entrypoint resolves alembic from settings.root_dir,
    not from the installed app package's parent directory."""
    from app.db import migrations

    captured: dict[str, object] = {}

    def fake_run(database_url: str, root_dir: Path) -> None:
        captured["url"] = database_url
        captured["root_dir"] = root_dir

    monkeypatch.setattr(migrations, "_run", fake_run)
    monkeypatch.setenv("PLLM_ROOT_DIR", str(tmp_path / "root"))
    reset_settings_cache()
    try:
        await migrations.run_migrations()
    finally:
        reset_settings_cache()
    assert captured["root_dir"] == tmp_path / "root"


def test_migrations_upgrade_a_relocated_tree(tmp_path: Path) -> None:
    """PLLM_ROOT_DIR is enough to run a real upgrade: the app finds
    alembic.ini/alembic/ (and nothing else) outside the source tree."""
    root = _relocated_root(tmp_path)
    url = f"sqlite:///{tmp_path}/relocated.db"
    _run(url, root)
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert "alembic_version" in tables
    assert "sessions" in tables


def test_alembic_head_matches_models(tmp_path: Path) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    engine = _upgraded_engine(tmp_path)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        diff = compare_metadata(ctx, Base.metadata)
    # alembic_version is alembic's own bookkeeping table; anything else
    # differing between migrations and models is drift.
    real = [d for d in diff if "alembic_version" not in str(d)]
    assert real == [], f"schema drift between migrations and models: {real}"


def test_ocr_cache_key_includes_prompt_fingerprint(tmp_path: Path) -> None:
    """Changing the OCR prompt must create a NEW cache row, not violate
    the unique index (the index must span the fingerprint)."""
    from sqlalchemy import inspect

    engine = _upgraded_engine(tmp_path)
    idx = {i["name"]: i for i in inspect(engine).get_indexes("ocr_results")}
    assert "prompt_fingerprint" in idx["ix_ocr_key"]["column_names"]
    assert idx["ix_ocr_key"]["unique"]


@pytest.mark.parametrize(
    ("table", "index", "columns"),
    [
        ("proposals", "ix_proposals_session", ["session_id"]),
        ("proposals", "ix_proposals_step", ["step_id"]),
        ("sessions", "ix_sessions_job", ["job_id"]),
        ("audit_log", "ix_audit_kind", ["kind", "id"]),
    ],
)
def test_hot_query_indexes_exist(
    tmp_path: Path, table: str, index: str, columns: list[str]
) -> None:
    from sqlalchemy import inspect

    engine = _upgraded_engine(tmp_path)
    idx = {i["name"]: i for i in inspect(engine).get_indexes(table)}
    assert index in idx, f"missing index {index} on {table}"
    assert idx[index]["column_names"] == columns
