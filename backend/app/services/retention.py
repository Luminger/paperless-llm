"""Data-retention sweeper (docs/privacy.md "Retention").

Without it the app DB grows forever: OcrResult rows carry the full OCR
text and per-page markdown, Session.message_history the complete agent
transcript (document content included, via tool results). Two sweeps
bound that, both governed by ``retention.*`` config:

1. **Archived sessions** (``archived_session_days``, opt-in): sessions
   archived longer than the window get their ``message_history``
   blanked. The session ROW stays — title, params, steps, proposals,
   journal and the audit trail are untouched; only the heavy transcript
   payload goes, and the purge itself is recorded in the audit log.
   When no other live session is bound to the same document, that
   document's OCR cache rows are deleted too (a cache miss re-OCRs;
   a blanked row would be a lying cache hit).
2. **Orphaned documents** (``orphaned_document_days``, default 30):
   OCR cache rows for documents that no longer exist in paperless.
   Detection is lazy and bounded — at most ``orphan_check_limit``
   existence checks per sweep, only for document ids whose newest OCR
   row is older than the window and that no active session references.
   Only a definitive 404 purges; connectivity errors abort the pass.

Hard constraints, on purpose:

- The **AppliedChange journal is never touched**. Revertibility is a
  core promise (docs/usage/proposals.md) and holds for archived
  sessions too ("their journal can still revert applied changes").
- Retention writes **no step/session state** — the step engine is the
  single writer of state (docs/state-machine.md). Only heavy payload
  columns are affected, and only on archived sessions, which the
  engine refuses new work for anyway.
- No schema changes: blanking a JSON column and deleting cache rows
  need none.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import EntityType, OcrResult, Session, utcnow
from app.paperless import PaperlessError, make_client
from app.services.audit import record

log = logging.getLogger(__name__)


def sweeper_enabled() -> bool:
    cfg = get_settings().retention
    return (
        cfg.archived_session_days is not None
        or cfg.orphaned_document_days is not None
    )


async def sweep() -> dict[str, int]:
    """One retention pass. Returns counters for logging/tests."""
    cfg = get_settings().retention
    stats = {"sessions_purged": 0, "ocr_rows_deleted": 0, "orphaned_documents": 0}
    if cfg.archived_session_days is not None:
        await _purge_archived_sessions(cfg.archived_session_days, stats)
    if cfg.orphaned_document_days is not None:
        await _purge_orphaned_ocr(
            cfg.orphaned_document_days, cfg.orphan_check_limit, stats
        )
    return stats


async def sweeper_loop() -> None:
    """Lifespan task: one sweep shortly after startup, then periodic."""
    cfg = get_settings().retention
    await asyncio.sleep(cfg.startup_delay_seconds)
    while True:
        try:
            stats = await sweep()
            if any(stats.values()):
                log.info("retention sweep: %s", stats)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the sweeper must survive hiccups
            log.exception("retention sweep failed")
        await asyncio.sleep(get_settings().retention.sweep_interval_hours * 3600)


# ----- archived sessions ----------------------------------------------


async def _purge_archived_sessions(days: int, stats: dict[str, int]) -> None:
    from app.db.session import session_scope

    cutoff = utcnow() - timedelta(days=days)
    async with session_scope() as db:
        sessions = (
            await db.scalars(
                select(Session).where(
                    Session.archived_at.is_not(None),
                    Session.archived_at < cutoff,
                    # Already-purged sessions carry an empty transcript
                    # and drop out of the sweep for free.
                    Session.message_history != [],
                )
            )
        ).all()
        for s in sessions:
            n_messages = len(s.message_history or [])
            s.message_history = []
            ocr_deleted = 0
            if s.entity_type == EntityType.document and s.entity_id is not None:
                ocr_deleted = await _drop_ocr_unless_needed(db, s.entity_id, cutoff)
            stats["sessions_purged"] += 1
            stats["ocr_rows_deleted"] += ocr_deleted
            # The audit trail records WHAT was purged — the skeleton
            # (session row, steps, proposals, journal) stays.
            await record(
                db,
                "retention",
                "session_purged",
                actor="system",
                session_id=s.id,
                messages=n_messages,
                ocr_rows=ocr_deleted,
                archived_at=s.archived_at.isoformat() if s.archived_at else None,
            )
        await db.commit()


async def _drop_ocr_unless_needed(
    db: AsyncSession, document_id: int, cutoff
) -> int:
    """Delete the document's OCR cache rows — unless some OTHER session
    still legitimately needs them (not archived, or archived more
    recently than the retention window)."""
    live = await db.scalar(
        select(func.count())
        .select_from(Session)
        .where(
            Session.entity_type == EntityType.document,
            Session.entity_id == document_id,
            (Session.archived_at.is_(None)) | (Session.archived_at >= cutoff),
        )
    )
    if live:
        return 0
    res = await db.execute(
        delete(OcrResult).where(OcrResult.document_id == document_id)
    )
    return res.rowcount or 0


# ----- orphaned documents ---------------------------------------------


async def _purge_orphaned_ocr(
    days: int, check_limit: int, stats: dict[str, int]
) -> None:
    from app.db.session import session_scope

    cutoff = utcnow() - timedelta(days=days)
    async with session_scope() as db:
        # Candidates: document ids whose NEWEST OCR row predates the
        # window (a doc still being worked on has fresh rows) and that
        # no active session references (never yank content out from
        # under a live review).
        newest = (
            select(
                OcrResult.document_id,
                func.max(OcrResult.created_at).label("newest"),
            )
            .group_by(OcrResult.document_id)
            .subquery()
        )
        active_docs = select(Session.entity_id).where(
            Session.entity_type == EntityType.document,
            Session.entity_id.is_not(None),
            Session.archived_at.is_(None),
        )
        candidates = (
            await db.scalars(
                select(newest.c.document_id)
                .where(
                    newest.c.newest < cutoff,
                    newest.c.document_id.not_in(active_docs),
                )
                .order_by(newest.c.newest)
                .limit(check_limit)
            )
        ).all()
    if not candidates:
        return

    orphans: list[int] = []
    async with make_client() as client:
        for doc_id in candidates:
            try:
                await client.get_document(doc_id)
            except PaperlessError as e:
                if e.status_code == 404:
                    orphans.append(doc_id)
                elif e.status_code is None:
                    # Connectivity trouble — nothing definitive, and no
                    # point hammering a dead endpoint. Next sweep retries.
                    log.warning("retention orphan check aborted: %s", e)
                    return
                # Any other HTTP status: not a definitive "gone", skip.
    if not orphans:
        return

    async with session_scope() as db:
        for doc_id in orphans:
            res = await db.execute(
                delete(OcrResult).where(OcrResult.document_id == doc_id)
            )
            deleted = res.rowcount or 0
            stats["orphaned_documents"] += 1
            stats["ocr_rows_deleted"] += deleted
            await record(
                db,
                "retention",
                "orphan_purged",
                actor="system",
                document_id=doc_id,
                ocr_rows=deleted,
            )
        await db.commit()
