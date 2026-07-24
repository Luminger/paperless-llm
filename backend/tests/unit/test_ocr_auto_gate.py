"""The born-digital auto-resolve: when every page came from the PDF's
own text layer AND matches the stored content, the OCR gate resolves
itself — analysis follows immediately (or the pipeline ends, for
OCR-only sessions). Anything less keeps the human gate."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import (
    AgentKind,
    EntityType,
    QueueLane,
    Session,
    Step,
    StepKind,
    StepState,
)
from app.llm.ocr import OcrOutcome
from app.services import pipeline as pipeline_mod
from app.services.pipeline import _exec_ocr
from app.services.steps import AWAIT_USER


def _outcome(**kw) -> OcrOutcome:
    base = dict(
        document_id=7,
        checksum="abc",
        model="test",
        pages=["native text"],
        text="native text",
        similarity=1.0,
        from_cache=False,
        timings=[{"pages": "1", "native": True, "count": 1, "duration_s": 0.0}],
        native_pages=1,
        truncated=False,
        total_pages=1,
        previous_content="native text",
    )
    return OcrOutcome(**{**base, **kw})


async def _session_with_ocr_step(
    db, params: dict | None = None
) -> tuple[Session, Step]:
    session = Session(
        agent_kind=AgentKind.document,
        entity_type=EntityType.document,
        entity_id=7,
        params=params or {},
    )
    db.add(session)
    await db.flush()
    step = Step(
        session_id=session.id,
        kind=StepKind.ocr,
        state=StepState.running,
        lane=QueueLane.interactive,
    )
    db.add(step)
    await db.commit()
    return session, step


async def _run(db, monkeypatch, outcome: OcrOutcome, params: dict | None = None):
    async def fake_run_ocr(*a, **kw):
        return outcome

    monkeypatch.setattr(pipeline_mod, "run_ocr", fake_run_ocr)
    session, step = await _session_with_ocr_step(db, params)
    verdict = await _exec_ocr(db, None, session, step)
    return verdict, session, step


async def _analysis_steps(db, session) -> list[Step]:
    return list(
        await db.scalars(
            select(Step).where(
                Step.session_id == session.id, Step.kind == StepKind.analysis
            )
        )
    )


async def test_all_native_matching_content_auto_resolves(db, monkeypatch):
    verdict, session, step = await _run(db, monkeypatch, _outcome())
    assert verdict is None  # no gate
    assert step.result["resolution"] == "auto_native"
    assert session.params["ocr_gate"] == "auto_native"
    [analysis] = await _analysis_steps(db, session)
    assert analysis.input["gate"] == "auto_native"


async def test_all_native_ocr_only_ends_the_pipeline(db, monkeypatch):
    verdict, session, _ = await _run(
        db, monkeypatch, _outcome(), params={"ocr_only": True}
    )
    assert verdict is None
    assert session.params["ocr_gate"] == "auto_native"
    assert await _analysis_steps(db, session) == []


async def test_low_similarity_keeps_the_gate(db, monkeypatch):
    """All-native but the stored content differs (e.g. paperless force-
    OCRed a digital PDF with tesseract): the user reviews the diff."""
    verdict, _, step = await _run(db, monkeypatch, _outcome(similarity=0.4))
    assert verdict == AWAIT_USER
    assert "resolution" not in step.result


async def test_partially_native_keeps_the_gate(db, monkeypatch):
    verdict, _, _ = await _run(
        db,
        monkeypatch,
        _outcome(pages=["native", "vlm"], native_pages=1, similarity=1.0),
    )
    assert verdict == AWAIT_USER


async def test_truncated_run_keeps_the_gate(db, monkeypatch):
    verdict, _, _ = await _run(
        db, monkeypatch, _outcome(truncated=True, total_pages=3, similarity=None)
    )
    assert verdict == AWAIT_USER


async def test_disabled_threshold_keeps_the_gate(db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(
        get_settings().llm.ocr, "native_auto_accept_similarity", None
    )
    verdict, _, _ = await _run(db, monkeypatch, _outcome())
    assert verdict == AWAIT_USER


async def test_native_result_is_recorded_on_the_step(db, monkeypatch):
    _, _, step = await _run(db, monkeypatch, _outcome())
    assert step.result["native_pages"] == 1
    assert step.result["pages"] == 1
