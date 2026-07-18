"""The migration chain IS the schema: prod upgrades via alembic while
tests create_all from the models — this test pins the two together so
they can never drift (missing index, stale enum, forgotten column)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine

from alembic import command
from app.db.models import Base

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def _upgraded_engine(tmp_path: Path):
    url = f"sqlite:///{tmp_path}/migrated.db"
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return create_engine(url)


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
    idx = {
        i["name"]: i for i in inspect(engine).get_indexes("ocr_results")
    }
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
