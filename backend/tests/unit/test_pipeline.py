"""Decision loop: auto-apply + auto-continuation (AUDIT SV-H1, SV-M5).

The auto path calls continue_after_decision from INSIDE the executor of
the step that produced the proposal — that step is committed 'running',
and before the fix the busy check counted it, so the continuation turn
was never created and auto jobs stopped after one change per document.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import (
    AgentKind,
    EntityType,
    Proposal,
    ProposalStatus,
    Session,
    Step,
    StepKind,
    StepState,
)
from app.services.pipeline import _maybe_auto_apply, continue_after_decision


async def _auto_session(db, **kw) -> Session:
    s = Session(
        agent_kind=AgentKind.document,
        entity_type=EntityType.document,
        entity_id=7,
        params={"apply_policy": "auto"},
        **kw,
    )
    db.add(s)
    await db.commit()
    return s


async def _running_step_with_applied_proposal(
    db, session: Session
) -> tuple[Step, Proposal]:
    step = Step(
        session_id=session.id, kind=StepKind.analysis, state=StepState.running
    )
    db.add(step)
    await db.flush()
    p = Proposal(
        session_id=session.id,
        step_id=step.id,
        kind="update_document_metadata",
        agent_payload={"document_id": 7, "title": "T"},
        status=ProposalStatus.applied,
        entity_type=EntityType.document,
        entity_id=7,
    )
    db.add(p)
    await db.commit()
    return step, p


async def test_continuation_excludes_the_triggering_step(db):
    session = await _auto_session(db)
    step, p = await _running_step_with_applied_proposal(db, session)

    # Without the exclusion the running trigger step blocks forever —
    # the pre-fix behavior (still correct for OTHER in-flight steps).
    assert await continue_after_decision(db, session, p) is None

    follow = await continue_after_decision(db, session, p, exclude_step_id=step.id)
    assert follow is not None
    assert follow.kind == StepKind.chat
    assert follow.input.get("auto") is True
    assert follow.lane == step.lane  # continuation stays in the job's lane


async def test_continuation_still_blocked_by_other_inflight_steps(db):
    session = await _auto_session(db)
    step, p = await _running_step_with_applied_proposal(db, session)
    other = Step(
        session_id=session.id, kind=StepKind.chat, state=StepState.pending
    )
    db.add(other)
    await db.commit()

    assert (
        await continue_after_decision(db, session, p, exclude_step_id=step.id)
        is None
    )


async def test_auto_apply_continues_the_session(db, monkeypatch):
    """End-to-end through _maybe_auto_apply: pending proposal applied
    (mocked) → continuation chat step exists afterwards."""
    session = await _auto_session(db)
    step = Step(
        session_id=session.id, kind=StepKind.analysis, state=StepState.running
    )
    db.add(step)
    await db.flush()
    p = Proposal(
        session_id=session.id,
        step_id=step.id,
        kind="update_document_metadata",
        agent_payload={"document_id": 7, "title": "T"},
        status=ProposalStatus.pending,
        entity_type=EntityType.document,
        entity_id=7,
    )
    db.add(p)
    await db.commit()

    async def fake_apply(paperless, dbs, prop, **kw):
        prop.status = ProposalStatus.applied
        return prop

    monkeypatch.setattr("app.services.pipeline.apply_proposal", fake_apply)
    await _maybe_auto_apply(db, None, session, step)

    chats = list(
        (
            await db.scalars(
                select(Step).where(
                    Step.session_id == session.id, Step.kind == StepKind.chat
                )
            )
        ).all()
    )
    assert len(chats) == 1 and chats[0].input.get("auto") is True


async def test_auto_apply_refuses_archived_sessions(db, monkeypatch):
    """AUDIT SV-M5: archiving mid-run must stop forward-apply."""
    session = await _auto_session(db, archived_at=datetime.now(UTC))
    step = Step(
        session_id=session.id, kind=StepKind.analysis, state=StepState.running
    )
    db.add(step)
    await db.flush()
    p = Proposal(
        session_id=session.id,
        step_id=step.id,
        kind="update_document_metadata",
        agent_payload={"document_id": 7, "title": "T"},
        status=ProposalStatus.pending,
        entity_type=EntityType.document,
        entity_id=7,
    )
    db.add(p)
    await db.commit()

    called = {"n": 0}

    async def fake_apply(*a, **kw):
        called["n"] += 1

    monkeypatch.setattr("app.services.pipeline.apply_proposal", fake_apply)
    await _maybe_auto_apply(db, None, session, step)
    assert called["n"] == 0  # nothing applied


async def test_auto_apply_sees_a_mid_turn_archive(db, monkeypatch):
    """Reinspection (SV-M5 follow-up): the executor's Session object is
    loaded at turn START — an archive committed while the LLM ran must
    still be seen (fresh read, not the cached ORM attribute)."""
    from sqlalchemy import update as sa_update

    from app.db.models import Session as SessionModel

    session = await _auto_session(db)  # not archived at load time
    step = Step(
        session_id=session.id, kind=StepKind.analysis, state=StepState.running
    )
    db.add(step)
    await db.flush()
    p = Proposal(
        session_id=session.id,
        step_id=step.id,
        kind="update_document_metadata",
        agent_payload={"document_id": 7, "title": "T"},
        status=ProposalStatus.pending,
        entity_type=EntityType.document,
        entity_id=7,
    )
    db.add(p)
    await db.commit()

    # The user archives WHILE the turn is running — a raw UPDATE that the
    # in-memory `session` object (expire_on_commit=False) never sees.
    await db.execute(
        sa_update(SessionModel)
        .where(SessionModel.id == session.id)
        .values(archived_at=datetime.now(UTC))
    )
    await db.commit()
    assert session.archived_at is None  # the stale attribute lies

    called = {"n": 0}

    async def fake_apply(*a, **kw):
        called["n"] += 1

    monkeypatch.setattr("app.services.pipeline.apply_proposal", fake_apply)
    await _maybe_auto_apply(db, None, session, step)
    assert called["n"] == 0  # the fresh read refused anyway


# ----- auto-apply scoping (injection guard) ---------------------------
#
# The propose tools accept arbitrary ids, so a prompt injection in one
# document's text could otherwise auto-apply changes to OTHER documents
# with zero review. Auto-apply is scoped to the session's own binding.


async def _pending_proposal(db, session, step, **kw) -> Proposal:
    p = Proposal(
        session_id=session.id,
        step_id=step.id,
        status=ProposalStatus.pending,
        **kw,
    )
    db.add(p)
    await db.commit()
    return p


async def _running_step(db, session) -> Step:
    step = Step(
        session_id=session.id, kind=StepKind.analysis, state=StepState.running
    )
    db.add(step)
    await db.commit()
    return step


def _patch_apply(monkeypatch, applied: list):
    async def fake_apply(paperless, dbs, prop, **kw):
        prop.status = ProposalStatus.applied
        applied.append(prop.id)
        return prop

    monkeypatch.setattr("app.services.pipeline.apply_proposal", fake_apply)


async def _deferral_audits(db) -> list:
    from app.db.models import AuditLog

    return list(
        (
            await db.scalars(
                select(AuditLog).where(AuditLog.action == "auto_apply_deferred")
            )
        ).all()
    )


async def test_auto_apply_same_document_proposal_applies(db, monkeypatch):
    session = await _auto_session(db)  # bound to document 7
    step = await _running_step(db, session)
    p = await _pending_proposal(
        db, session, step,
        kind="update_document_metadata",
        agent_payload={"document_id": 7, "title": "T"},
        entity_type=EntityType.document, entity_id=7,
    )
    applied: list = []
    _patch_apply(monkeypatch, applied)
    await _maybe_auto_apply(db, None, session, step)
    assert applied == [p.id]
    assert not await _deferral_audits(db)


async def test_auto_apply_cross_document_proposal_stays_pending_and_audited(
    db, monkeypatch
):
    """The injection scenario: the turn (bound to doc 7) emitted a
    proposal for doc 99 — it must NOT be applied without review, and
    the deferral must leave an audit trail."""
    session = await _auto_session(db)
    step = await _running_step(db, session)
    p = await _pending_proposal(
        db, session, step,
        kind="update_document_metadata",
        agent_payload={"document_id": 99, "title": "pwned"},
        entity_type=EntityType.document, entity_id=99,
    )
    applied: list = []
    _patch_apply(monkeypatch, applied)
    await _maybe_auto_apply(db, None, session, step)
    assert applied == []
    await db.refresh(p)
    assert p.status == ProposalStatus.pending  # awaits human review
    audits = await _deferral_audits(db)
    assert len(audits) == 1
    assert audits[0].detail["proposal_id"] == p.id
    assert audits[0].detail["entity_id"] == 99
    # And no auto-continuation while the deferred proposal is open —
    # continue_after_decision's open-proposal check keeps the session
    # honestly waiting for the human.
    chats = (
        await db.scalars(
            select(Step).where(
                Step.session_id == session.id, Step.kind == StepKind.chat
            )
        )
    ).all()
    assert list(chats) == []


async def test_auto_apply_create_entity_scoped_to_own_document(db, monkeypatch):
    """create_entity has no target entity id; in a document session it is
    in scope only when every assigned document is the session's own."""
    session = await _auto_session(db)
    step = await _running_step(db, session)
    own = await _pending_proposal(
        db, session, step,
        kind="create_entity",
        agent_payload={"kind": "create_entity", "entity_type": "tag",
                       "name": "invoices", "assign_to_documents": [7]},
        entity_type=EntityType.tag, entity_id=None,
    )
    cross = await _pending_proposal(
        db, session, step,
        kind="create_entity",
        agent_payload={"kind": "create_entity", "entity_type": "tag",
                       "name": "pwned", "assign_to_documents": [7, 99]},
        entity_type=EntityType.tag, entity_id=None,
    )
    applied: list = []
    _patch_apply(monkeypatch, applied)
    await _maybe_auto_apply(db, None, session, step)
    assert applied == [own.id]
    await db.refresh(cross)
    assert cross.status == ProposalStatus.pending
    audits = await _deferral_audits(db)
    assert [a.detail["proposal_id"] for a in audits] == [cross.id]


async def test_auto_apply_internal_kind_unaffected_by_scoping(db, monkeypatch):
    """Internal proposals (replace_content) carry the session's own
    binding at creation — the scope guard must not change how they are
    handled (they always target the session's document)."""
    session = await _auto_session(db)
    step = await _running_step(db, session)
    p = await _pending_proposal(
        db, session, step,
        kind="replace_content",
        agent_payload={"kind": "replace_content", "document_id": 7,
                       "content": "text"},
        entity_type=EntityType.document, entity_id=7,
    )
    applied: list = []
    _patch_apply(monkeypatch, applied)
    await _maybe_auto_apply(db, None, session, step)
    assert applied == [p.id]
    assert not await _deferral_audits(db)


async def test_auto_apply_entity_session_scoped_to_its_entity(db, monkeypatch):
    """Entity sessions: proposals targeting the reviewed entity itself
    (here: merge with the session's tag as SOURCE) keep auto-applying —
    that fan-out is the queued job. Edits of a DIFFERENT entity stay
    pending."""
    session = Session(
        agent_kind=AgentKind.tag,
        entity_type=EntityType.tag,
        entity_id=5,
        params={"apply_policy": "auto"},
    )
    db.add(session)
    await db.commit()
    step = await _running_step(db, session)
    own = await _pending_proposal(
        db, session, step,
        kind="merge_entities",
        agent_payload={"kind": "merge_entities", "entity_type": "tag",
                       "source_id": 5, "target_id": 9},
        entity_type=EntityType.tag, entity_id=5,
    )
    other = await _pending_proposal(
        db, session, step,
        kind="delete_entity",
        agent_payload={"kind": "delete_entity", "entity_type": "tag",
                       "entity_id": 12},
        entity_type=EntityType.tag, entity_id=12,
    )
    applied: list = []
    _patch_apply(monkeypatch, applied)
    await _maybe_auto_apply(db, None, session, step)
    assert applied == [own.id]
    await db.refresh(other)
    assert other.status == ProposalStatus.pending
    audits = await _deferral_audits(db)
    assert [a.detail["proposal_id"] for a in audits] == [other.id]


async def test_audit_failure_does_not_poison_the_session(db):
    """AUDIT SV-M4: a failed audit flush must roll back to a savepoint —
    the caller's transaction stays usable (previously every later
    statement raised PendingRollbackError, recording successful turns
    as failed steps)."""
    from app.db.models import AgentKind, Session
    from app.services.audit import record

    # Unserializable detail -> flush inside record() fails.
    await record(db, "test", "boom", commit=False, bad=object())

    # The caller can still do real work afterwards.
    s = Session(agent_kind=AgentKind.document)
    db.add(s)
    await db.commit()
    assert s.id is not None


async def test_counter_failure_does_not_poison_the_session(db):
    """Same savepoint guarantee for counters."""
    from unittest.mock import patch

    from sqlalchemy.exc import IntegrityError

    from app.db.models import AgentKind, Session
    from app.services import counters

    # Force the insert-race branch: UPDATE matches nothing, and the
    # insert flush raises IntegrityError (as if another worker won).
    real_flush = type(db).flush
    calls = {"n": 0}

    async def flaky_flush(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("dup", None, Exception("unique"))
        return await real_flush(self)

    with patch.object(type(db), "flush", flaky_flush):
        await counters.increment(db, test_counter=1)

    s = Session(agent_kind=AgentKind.document)
    db.add(s)
    await db.commit()
    assert s.id is not None
