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
