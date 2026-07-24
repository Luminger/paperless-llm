"""PDF/image primitives: page counting, rendering, native text, and the
born-digital page classifier. Fitz-free: rendering and text extraction
go through pypdfium2 (PDFium), classification through pdfminer.six —
the same approach OCRmyPDF's --skip-text/--redo-ocr detection uses.

Everything here is blocking CPU work — call from a worker thread.

Thread-safety: PDFium is NOT thread-safe (unlike MuPDF). All pdfium
calls take a module-level lock; pdfminer is pure Python and safe.
"""

from __future__ import annotations

import io
import logging
import threading
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image

log = logging.getLogger(__name__)

_PDFIUM_LOCK = threading.Lock()

# Text render modes that produce no marks on the page (PDF 32000-1
# §9.3.6): 3 = neither fill nor stroke (the classic OCR text layer),
# 7 = add to clipping path only.
_INVISIBLE_RENDER_MODES = (3, 7)

# Classifier thresholds. A page is "digital" when it has a real,
# VISIBLE native text layer and is not mostly bitmap: previously-OCRed
# scans have plenty of text too — but it is invisible (Tr 3) and sits
# under/over a full-page image, so they classify as scans and get the
# full VLM treatment (the --redo-ocr semantics).
_MIN_VISIBLE_CHARS = 40
_MAX_IMAGE_COVERAGE = 0.5


def is_pdf(content_type: str) -> bool:
    return "pdf" in (content_type or "").lower()


@dataclass(frozen=True)
class PageProfile:
    """What the classifier learned about one page."""

    visible_chars: int
    invisible_chars: int
    image_coverage: float  # 0..1, bitmap area / page area (capped)

    @property
    def digital(self) -> bool:
        return (
            self.visible_chars >= _MIN_VISIBLE_CHARS
            and self.image_coverage < _MAX_IMAGE_COVERAGE
        )


# ----- pypdfium2: page count, rendering, native text -------------------


def page_count(data: bytes, content_type: str) -> int:
    if not is_pdf(content_type):
        return _image_frame_count(data)
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(data)
        try:
            return len(doc)
        finally:
            doc.close()


def render_page(
    data: bytes, content_type: str, index: int, dpi: int, rotation: int = 0
) -> bytes:
    """Render page ``index`` (0-based) as PNG. ``rotation`` is degrees
    CLOCKWISE (0/90/180/270) to apply on top of the page's own /Rotate."""
    if not is_pdf(content_type):
        return _render_image_frame(data, index, rotation)
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(data)
        try:
            page = doc[index]
            bitmap = page.render(scale=dpi / 72.0, rotation=rotation)
            pil = bitmap.to_pil()
        finally:
            doc.close()
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return buf.getvalue()


def native_page_texts(data: bytes, content_type: str) -> list[str]:
    """The embedded text layer of every page, in page order. PDFium's
    extractor (Chrome's) — used verbatim for born-digital pages instead
    of a VLM round-trip. Images have no text layer."""
    if not is_pdf(content_type):
        return [""] * _image_frame_count(data)
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(data)
        out: list[str] = []
        try:
            for page in doc:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range() or ""
                finally:
                    textpage.close()
                out.append(text.replace("\r\n", "\n").replace("\r", "\n").strip())
            return out
        finally:
            doc.close()


# ----- Pillow: image originals (always scans by construction) ----------


def _image_frame_count(data: bytes) -> int:
    with Image.open(io.BytesIO(data)) as img:
        return getattr(img, "n_frames", 1)


def _render_image_frame(data: bytes, index: int, rotation: int) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        if index:
            img.seek(index)
        frame = img.convert("RGB")
    if rotation:
        # PIL rotates counter-clockwise; our contract is clockwise.
        frame = frame.rotate(-rotation, expand=True)
    buf = io.BytesIO()
    frame.save(buf, format="PNG")
    return buf.getvalue()


# ----- pdfminer.six: the born-digital classifier -----------------------


def classify_pages(data: bytes, content_type: str) -> list[PageProfile]:
    """Per-page classification. Fail-safe: anything that goes wrong
    (corrupt PDF, encryption, pdfminer hiccup) classifies the affected
    page(s) as scans — the VLM path handles everything, just slower."""
    if not is_pdf(content_type):
        return [
            PageProfile(visible_chars=0, invisible_chars=0, image_coverage=1.0)
        ] * _image_frame_count(data)
    try:
        return _classify_pdf(data)
    except Exception:  # noqa: BLE001 — classification is best-effort
        log.warning("page classification failed; treating all pages as scans", exc_info=True)
        try:
            n = page_count(data, content_type)
        except Exception:  # noqa: BLE001
            n = 0
        return [PageProfile(visible_chars=0, invisible_chars=0, image_coverage=1.0)] * n


def _classify_pdf(data: bytes) -> list[PageProfile]:
    from pdfminer.pdfdevice import PDFDevice
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser
    from pdfminer.utils import apply_matrix_pt

    class _Probe(PDFDevice):
        """Counts visible/invisible text and accumulates bitmap area.
        Same signals OCRmyPDF's pdfinfo derives (reimplemented — their
        implementation is MPL-2.0)."""

        def __init__(self, rsrcmgr):
            super().__init__(rsrcmgr)
            self.visible_chars = 0
            self.invisible_chars = 0
            self.image_area = 0.0

        def render_string(self, textstate, seq, ncs, graphicstate):
            n = 0
            font = textstate.font
            for item in seq:
                if not isinstance(item, bytes):
                    continue
                try:
                    n += sum(1 for _ in font.decode(item))
                except Exception:  # noqa: BLE001 — broken font: approximate
                    n += len(item)
            if textstate.render in _INVISIBLE_RENDER_MODES:
                self.invisible_chars += n
            else:
                self.visible_chars += n

        def render_image(self, name, stream):
            # Image space is the unit square mapped through the CTM.
            xs, ys = zip(
                *(
                    apply_matrix_pt(self.ctm, p)
                    for p in ((0, 0), (1, 0), (0, 1), (1, 1))
                ),
                strict=True,
            )
            self.image_area += (max(xs) - min(xs)) * (max(ys) - min(ys))

    parser = PDFParser(io.BytesIO(data))
    document = PDFDocument(parser)
    rsrcmgr = PDFResourceManager()
    profiles: list[PageProfile] = []
    for page in PDFPage.create_pages(document):
        device = _Probe(rsrcmgr)
        try:
            PDFPageInterpreter(rsrcmgr, device).process_page(page)
        except Exception:  # noqa: BLE001 — one broken page must not
            # poison the rest; unknown content -> scan.
            log.warning("classifier failed on page %d", len(profiles) + 1, exc_info=True)
            profiles.append(
                PageProfile(visible_chars=0, invisible_chars=0, image_coverage=1.0)
            )
            continue
        x0, y0, x1, y1 = page.mediabox
        area = abs((x1 - x0) * (y1 - y0)) or 1.0
        profiles.append(
            PageProfile(
                visible_chars=device.visible_chars,
                invisible_chars=device.invisible_chars,
                image_coverage=min(device.image_area / area, 1.0),
            )
        )
    return profiles
