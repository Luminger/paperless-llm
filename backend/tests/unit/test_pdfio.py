"""The fitz-free PDF backend: page counting, rendering, native text,
and the born-digital classifier (app.pdfio).

The classifier's contract: only pages with a real VISIBLE text layer
are digital — invisible (Tr 3) OCR layers over scans classify as scans
(OCRmyPDF --redo-ocr semantics), because that stale layer is exactly
what the VLM re-OCR is meant to replace."""

from __future__ import annotations

import io

from PIL import Image

from app import pdfio
from tests.fixtures.pdfs import (
    DIGITAL_TEXT,
    digital_pdf,
    mixed_pdf,
    ocr_layered_pdf,
    scan_pdf,
)

PDF = "application/pdf"


def test_digital_page_classifies_digital():
    [p] = pdfio.classify_pages(digital_pdf(), PDF)
    assert p.digital
    assert p.visible_chars >= 40
    assert p.image_coverage == 0.0


def test_scan_page_classifies_scan():
    [p] = pdfio.classify_pages(scan_pdf(), PDF)
    assert not p.digital
    assert p.visible_chars == 0
    assert p.image_coverage > 0.9


def test_invisible_ocr_layer_classifies_scan():
    """The corpus-critical case: a previously-OCRed scan has plenty of
    text — but it is invisible. Naive has-text checks say 'digital';
    the classifier must say 'scan' so the VLM replaces the stale layer."""
    [p] = pdfio.classify_pages(ocr_layered_pdf(), PDF)
    assert not p.digital
    assert p.visible_chars == 0
    assert p.invisible_chars > 0
    assert p.image_coverage > 0.9


def test_short_visible_text_is_not_digital():
    """A scan with a tiny visible caption must not skip OCR."""
    [p] = pdfio.classify_pages(digital_pdf("Page 1"), PDF)
    assert p.visible_chars > 0
    assert not p.digital


def test_mixed_document_classifies_per_page():
    digital, scan = pdfio.classify_pages(mixed_pdf(), PDF)
    assert digital.digital
    assert not scan.digital


def test_native_text_extraction():
    texts = pdfio.native_page_texts(mixed_pdf(), PDF)
    assert len(texts) == 2
    assert DIGITAL_TEXT in texts[0]
    assert texts[1] == ""


def test_native_text_includes_invisible_layer():
    """PDFium extracts invisible text too — which is exactly why the
    CLASSIFIER gates the native path, not the extractor."""
    [text] = pdfio.native_page_texts(ocr_layered_pdf(), PDF)
    assert "previous tesseract" in text


def test_page_count_and_render():
    data = mixed_pdf()
    assert pdfio.page_count(data, PDF) == 2
    png = pdfio.render_page(data, PDF, 1, dpi=72)
    img = Image.open(io.BytesIO(png))
    assert img.size == (200, 300)  # 72 dpi == PDF points
    rotated = pdfio.render_page(data, PDF, 1, dpi=72, rotation=90)
    assert Image.open(io.BytesIO(rotated)).size == (300, 200)


def test_image_original_is_always_a_scan():
    buf = io.BytesIO()
    Image.new("RGB", (40, 60), "white").save(buf, format="PNG")
    data = buf.getvalue()
    assert pdfio.page_count(data, "image/png") == 1
    [p] = pdfio.classify_pages(data, "image/png")
    assert not p.digital
    assert pdfio.native_page_texts(data, "image/png") == [""]
    png = pdfio.render_page(data, "image/png", 0, dpi=150)
    assert Image.open(io.BytesIO(png)).size == (40, 60)


def test_corrupt_pdf_classifies_scan_not_raises():
    profiles = pdfio.classify_pages(b"%PDF-1.4 garbage", PDF)
    assert all(not p.digital for p in profiles)
