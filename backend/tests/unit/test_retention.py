"""Retention sweeper: purges exactly what docs/privacy.md promises —
long-archived transcripts and orphaned OCR caches — and nothing else.

File-backed sqlite because the sweeper opens its own sessions via the
app's global engine."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings, reset_settings_cache
from app.db.models import (
    AgentKind,
    AppliedChange,
    AuditLog,
    EntityType,
    OcrResult,
    Proposal,
    ProposalStatus,
    Session,
    SessionStatus,
    utcnow,
)
from app.db.session import dispose_engine, init_db, session_scope
from app.paperless import PaperlessError
from app.services import retention

DAYS = 30


@pytest.fixture
async def retention_db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PLLM_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/s.sqlite3"
    )
    monkeypatch.setenv("PLLM_RETENTION__ARCHIVED_SESSION_DAYS", str(DAYS))
    monkeypatch.setenv("PLLM_RETENTION__ORPHANED_DOCUMENT_DAYS", str(DAYS))
    reset_settings_cache()
    await dispose_engine()
    await init_db()
    yield
    await dispose_engine()
    reset_settings_cache()


class FakeClient:
    """make_client stand-in: 404s for `missing` ids, 200 otherwise."""

    def __init__(self, missing: set[int] = frozenset()):
        self.missing = set(missing)
        self.calls: list[int] = []

    def __call__(self):  # make_client() factory shape
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get_document(self, doc_id: int):
        self.calls.append(doc_id)
        if doc_id in self.missing:
            raise PaperlessError("gone", status_code=404)
        return {"id": doc_id}


def _session(*, archived_days: int | None, doc_id: int | None = None, **kw):
    return Session(
        agent_kind=AgentKind.document,
        entity_type=EntityType.document if doc_id is not None else None,
        entity_id=doc_id,
        status=SessionStatus.idle,
        message_history=[{"role": "user", "content": "full document text"}],
        archived_at=(
            utcnow() - timedelta(days=archived_days)
            if archived_days is not None
            else None
        ),
        **kw,
    )


def _ocr(doc_id: int, *, age_days: int = 0, checksum: str = "c1") -> OcrResult:
    return OcrResult(
        document_id=doc_id,
        checksum=checksum,
        model="m",
        prompt_version=1,
        pages=["# page 1"],
        text="full ocr text",
        created_at=utcnow() - timedelta(days=age_days),
    )


async def _audit_actions() -> list[str]:
    async with session_scope() as db:
        return list(
            (await db.scalars(select(AuditLog.action).where(AuditLog.kind == "retention"))).all()
        )


async def test_purges_long_archived_session_transcript(retention_db, monkeypatch):
    monkeypatch.setattr(retention, "make_client", FakeClient())
    async with session_scope() as db:
        s = _session(archived_days=DAYS * 2, doc_id=7, title="old")
        db.add(s)
        db.add(_ocr(7))
        await db.commit()
        sid, archived_at = s.id, s.archived_at

    stats = await retention.sweep()
    assert stats["sessions_purged"] == 1
    assert stats["ocr_rows_deleted"] == 1

    async with session_scope() as db:
        row = await db.get(Session, sid)
        # Heavy payload gone, the skeleton and its state intact.
        assert row.message_history == []
        assert row.title == "old"
        assert row.status == SessionStatus.idle
        assert row.archived_at.replace(tzinfo=None) == archived_at.replace(tzinfo=None)
        assert (await db.scalar(select(OcrResult))) is None
        audit = await db.scalar(
            select(AuditLog).where(AuditLog.kind == "retention")
        )
        assert audit is not None
        assert audit.action == "session_purged"
        assert audit.detail["session_id"] == sid
        assert audit.detail["messages"] == 1


async def test_leaves_active_and_recently_archived_alone(retention_db, monkeypatch):
    monkeypatch.setattr(retention, "make_client", FakeClient())
    async with session_scope() as db:
        db.add(_session(archived_days=None, doc_id=1))  # active, never archived
        db.add(_session(archived_days=2, doc_id=2))  # archived, inside window
        await db.commit()

    stats = await retention.sweep()
    assert stats["sessions_purged"] == 0

    async with session_scope() as db:
        rows = (await db.scalars(select(Session))).all()
        assert all(r.message_history for r in rows)
    assert await _audit_actions() == []


async def test_journal_never_touched(retention_db, monkeypatch):
    """Revertibility is a core promise: the purge blanks the transcript
    but leaves the AppliedChange snapshots byte-for-byte intact."""
    monkeypatch.setattr(retention, "make_client", FakeClient())
    async with session_scope() as db:
        s = _session(archived_days=DAYS * 2, doc_id=9)
        db.add(s)
        await db.flush()
        p = Proposal(
            session_id=s.id,
            kind="replace_content",
            agent_payload={"content": "new"},
            status=ProposalStatus.applied,
        )
        db.add(p)
        await db.flush()
        db.add(
            AppliedChange(
                proposal_id=p.id,
                paperless_before={"content": "the original full text"},
                paperless_after={"content": "new"},
            )
        )
        await db.commit()
        sid = s.id

    await retention.sweep()

    async with session_scope() as db:
        assert (await db.get(Session, sid)).message_history == []
        change = await db.scalar(select(AppliedChange))
        assert change.paperless_before == {"content": "the original full text"}
        assert change.paperless_after == {"content": "new"}
        assert (await db.scalar(select(Proposal))).agent_payload == {"content": "new"}


async def test_shared_document_ocr_survives_purge(retention_db, monkeypatch):
    """A live session on the same document keeps the OCR cache."""
    monkeypatch.setattr(retention, "make_client", FakeClient())
    async with session_scope() as db:
        db.add(_session(archived_days=DAYS * 2, doc_id=5))
        db.add(_session(archived_days=None, doc_id=5))  # still active
        db.add(_ocr(5))
        await db.commit()

    stats = await retention.sweep()
    assert stats["sessions_purged"] == 1
    assert stats["ocr_rows_deleted"] == 0
    async with session_scope() as db:
        assert (await db.scalar(select(OcrResult))) is not None


async def test_orphan_sweep_deletes_404_documents_only(retention_db, monkeypatch):
    fake = FakeClient(missing={2})
    monkeypatch.setattr(retention, "make_client", fake)
    async with session_scope() as db:
        db.add(_ocr(1, age_days=DAYS * 2))  # old, still exists
        db.add(_ocr(2, age_days=DAYS * 2))  # old, 404s -> purged
        db.add(_ocr(2, age_days=DAYS * 2, checksum="c2"))
        db.add(_ocr(3, age_days=0))  # fresh -> not even checked
        await db.commit()

    stats = await retention.sweep()
    assert stats["orphaned_documents"] == 1
    assert stats["ocr_rows_deleted"] == 2
    assert 3 not in fake.calls

    async with session_scope() as db:
        remaining = {r.document_id for r in (await db.scalars(select(OcrResult))).all()}
        assert remaining == {1, 3}
        audit = await db.scalar(select(AuditLog).where(AuditLog.kind == "retention"))
        assert audit.action == "orphan_purged"
        assert audit.detail == {"document_id": 2, "ocr_rows": 2}


async def test_orphan_sweep_skips_docs_with_active_sessions(retention_db, monkeypatch):
    fake = FakeClient(missing={4})
    monkeypatch.setattr(retention, "make_client", fake)
    async with session_scope() as db:
        db.add(_ocr(4, age_days=DAYS * 2))
        db.add(_session(archived_days=None, doc_id=4))  # live review
        await db.commit()

    stats = await retention.sweep()
    assert stats["orphaned_documents"] == 0
    assert fake.calls == []  # never even asked paperless
    async with session_scope() as db:
        assert (await db.scalar(select(OcrResult))) is not None


async def test_orphan_sweep_aborts_on_connectivity_errors(retention_db, monkeypatch):
    class DownClient(FakeClient):
        async def get_document(self, doc_id: int):
            self.calls.append(doc_id)
            raise PaperlessError("paperless request failed: connect")  # no status

    fake = DownClient()
    monkeypatch.setattr(retention, "make_client", fake)
    async with session_scope() as db:
        db.add(_ocr(6, age_days=DAYS * 2))
        await db.commit()

    stats = await retention.sweep()
    assert stats["orphaned_documents"] == 0
    async with session_scope() as db:
        assert (await db.scalar(select(OcrResult))) is not None


async def test_disabled_config_is_a_noop(retention_db, monkeypatch):
    cfg = get_settings().retention
    monkeypatch.setattr(cfg, "archived_session_days", None)
    monkeypatch.setattr(cfg, "orphaned_document_days", None)

    class Exploding:
        def __call__(self):  # pragma: no cover - must never run
            raise AssertionError("disabled sweep must not talk to paperless")

    monkeypatch.setattr(retention, "make_client", Exploding())
    async with session_scope() as db:
        db.add(_session(archived_days=DAYS * 10, doc_id=8))
        db.add(_ocr(8, age_days=DAYS * 10))
        await db.commit()

    assert not retention.sweeper_enabled()
    stats = await retention.sweep()
    assert stats == {
        "sessions_purged": 0,
        "ocr_rows_deleted": 0,
        "orphaned_documents": 0,
    }
    async with session_scope() as db:
        assert (await db.scalar(select(Session))).message_history
        assert (await db.scalar(select(OcrResult))) is not None
    assert await _audit_actions() == []
