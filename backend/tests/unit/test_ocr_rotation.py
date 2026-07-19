"""OCR auto-rotate: flipped scans are detected (tesseract OSD) and
re-rendered upright before the vision model sees them."""

from __future__ import annotations

import subprocess

import fitz
import pytest

from app.llm import ocr as ocr_mod
from app.llm.ocr import detect_rotation, render_page_range


def _pdf_with_text() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    page.insert_text((50, 100), "Hello OCR")
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture(autouse=True)
def _fresh_tesseract_cache():
    ocr_mod._tesseract_available.cache_clear()
    yield
    ocr_mod._tesseract_available.cache_clear()


def test_detect_rotation_parses_osd(monkeypatch):
    monkeypatch.setattr(ocr_mod.shutil, "which", lambda _: "/usr/bin/tesseract")

    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(
            a, 0,
            stdout=b"Page number: 0\nOrientation in degrees: 180\nRotate: 180\n"
                   b"Orientation confidence: 22.5\n",
            stderr=b"",
        )

    monkeypatch.setattr(ocr_mod.subprocess, "run", fake_run)
    assert detect_rotation(b"png") == 180


def test_detect_rotation_without_tesseract_is_zero(monkeypatch):
    monkeypatch.setattr(ocr_mod.shutil, "which", lambda _: None)
    assert detect_rotation(b"png") == 0


def test_detect_rotation_swallows_subprocess_failure(monkeypatch):
    monkeypatch.setattr(ocr_mod.shutil, "which", lambda _: "/usr/bin/tesseract")

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired("tesseract", 30)

    monkeypatch.setattr(ocr_mod.subprocess, "run", boom)
    assert detect_rotation(b"png") == 0


def test_render_applies_detected_rotation(monkeypatch):
    """A page whose content OSD says is upside down comes back rotated
    (different raster) and the applied rotation is reported."""
    data = _pdf_with_text()
    plain = render_page_range(data, "application/pdf", 72, 0, 1)
    assert plain == [(plain[0][0], 0)]  # no auto_rotate -> no detection

    monkeypatch.setattr(ocr_mod, "detect_rotation", lambda _png: 180)
    rotated = render_page_range(data, "application/pdf", 72, 0, 1, auto_rotate=True)
    assert rotated[0][1] == 180
    assert rotated[0][0] != plain[0][0]  # actually re-rendered

    monkeypatch.setattr(ocr_mod, "detect_rotation", lambda _png: 0)
    upright = render_page_range(data, "application/pdf", 72, 0, 1, auto_rotate=True)
    assert upright[0][1] == 0
    assert upright[0][0] == plain[0][0]
