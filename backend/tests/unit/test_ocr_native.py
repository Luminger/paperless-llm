"""The born-digital gate inside run_ocr: digital pages read their
native text layer (zero VLM calls), scans go to the vision model, and
mixed documents split page-by-page with correct ordering."""

from __future__ import annotations

import asyncio

import pytest
import respx
from httpx import Response
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.config import OcrProfile
from app.llm import ocr as ocr_mod
from app.llm.ocr import _span_label, run_ocr
from tests.conftest import PAPERLESS_URL
from tests.fixtures.pdfs import (
    DIGITAL_TEXT,
    digital_pdf,
    mixed_pdf,
    ocr_layered_pdf,
)

DOC = {
    "id": 7,
    "title": "doc",
    "content": DIGITAL_TEXT,
    "tags": [],
    "correspondent": None,
    "document_type": None,
    "storage_path": None,
    "created": "2024-04-17",
    "custom_fields": [],
}


def _mock_paperless(pdf: bytes, content: str = DIGITAL_TEXT) -> None:
    respx.get(f"{PAPERLESS_URL}/api/documents/7/").mock(
        return_value=Response(200, json=DOC | {"content": content})
    )
    respx.get(f"{PAPERLESS_URL}/api/documents/7/download/").mock(
        return_value=Response(
            200, content=pdf, headers={"content-type": "application/pdf"}
        )
    )


@pytest.fixture
def vlm(monkeypatch):
    """ocr_model() backed by a FunctionModel returning a fixed string;
    records how many completions actually ran."""
    calls: list[list] = []

    async def fn(messages, info: AgentInfo) -> ModelResponse:
        calls.append(messages)
        return ModelResponse(parts=[TextPart("VLM TRANSCRIPT")])

    profile = OcrProfile()

    def fake_ocr_model():
        return FunctionModel(fn), None, profile, asyncio.Semaphore(1)

    monkeypatch.setattr(ocr_mod, "ocr_model", fake_ocr_model)
    # Auto-rotate would shell out to tesseract on rendered pages.
    profile.auto_rotate = False
    return calls, profile


@respx.mock
async def test_digital_document_skips_the_vlm(db, paperless_client, vlm):
    calls, _ = vlm
    _mock_paperless(digital_pdf())
    outcome = await run_ocr(paperless_client, db, 7)
    assert calls == []  # not a single completion
    assert outcome.native_pages == 1
    assert outcome.pages == [DIGITAL_TEXT]
    assert outcome.similarity == pytest.approx(1.0)
    assert outcome.timings == [
        {"pages": "1", "native": True, "count": 1, "duration_s": 0.0}
    ]


@respx.mock
async def test_invisible_ocr_layer_goes_to_the_vlm(db, paperless_client, vlm):
    """A previously-OCRed scan has (invisible) text — it must still be
    re-OCRed, not short-circuited through the stale tesseract layer."""
    calls, _ = vlm
    _mock_paperless(ocr_layered_pdf())
    outcome = await run_ocr(paperless_client, db, 7)
    assert len(calls) == 1
    assert outcome.native_pages == 0
    assert outcome.text == "VLM TRANSCRIPT"


@respx.mock
async def test_mixed_document_splits_per_page(db, paperless_client, vlm):
    calls, _ = vlm
    _mock_paperless(mixed_pdf())
    outcome = await run_ocr(paperless_client, db, 7)
    assert len(calls) == 1  # only the scan page
    assert outcome.native_pages == 1
    # Page order preserved: native page 1, transcribed page 2.
    assert outcome.pages == [DIGITAL_TEXT, "VLM TRANSCRIPT"]
    assert outcome.text == f"{DIGITAL_TEXT}\n\nVLM TRANSCRIPT"
    native, batch = outcome.timings
    assert native == {"pages": "1", "native": True, "count": 1, "duration_s": 0.0}
    assert batch["pages"] == "2"


@respx.mock
async def test_force_vlm_bypasses_the_gate(db, paperless_client, vlm):
    calls, _ = vlm
    _mock_paperless(digital_pdf())
    outcome = await run_ocr(paperless_client, db, 7, force_vlm=True)
    assert len(calls) == 1
    assert outcome.native_pages == 0
    assert outcome.text == "VLM TRANSCRIPT"


@respx.mock
async def test_native_text_profile_switch_disables_gate(db, paperless_client, vlm):
    calls, profile = vlm
    profile.native_text = False
    _mock_paperless(digital_pdf())
    outcome = await run_ocr(paperless_client, db, 7)
    assert len(calls) == 1
    assert outcome.native_pages == 0


@respx.mock
async def test_cache_round_trips_native_pages(db, paperless_client, vlm):
    calls, _ = vlm
    _mock_paperless(mixed_pdf())
    first = await run_ocr(paperless_client, db, 7)
    cached = await run_ocr(paperless_client, db, 7)
    assert len(calls) == 1  # second run served from cache
    assert cached.from_cache
    assert cached.native_pages == first.native_pages == 1
    assert cached.text == first.text


def test_span_label():
    assert _span_label([1]) == "1"
    assert _span_label([1, 2, 3]) == "1-3"
    assert _span_label([2, 5, 6, 7]) == "2, 5-7"
