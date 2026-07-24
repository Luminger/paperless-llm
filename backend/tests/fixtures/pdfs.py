"""Hand-rolled PDF builders for OCR tests — no PDF library needed.

Corpus archetypes:
- digital: visible native text, no images
- scan: one full-page image, no text
- tesseract-layer: full-page image + INVISIBLE text (Tr 3) — a
  previously-OCRed scan, the case naive has-text checks misclassify
- mixed: digital page followed by a scan page
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

PAGE_W, PAGE_H = 200, 300


@dataclass
class PageSpec:
    text: str | None = None
    invisible: bool = False  # render the text with Tr 3 (no marks)
    image: bool = False  # full-page image


def _stream(content: bytes) -> bytes:
    data = zlib.compress(content)
    return (
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data)
        + data
        + b"\nendstream"
    )


_FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

_GRAY_IMAGE = (
    b"<< /Type /XObject /Subtype /Image /Width 8 /Height 8 "
    b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 64 >>\nstream\n"
    + bytes(range(0, 256, 4))[:64]
    + b"\nendstream"
)


def _content(spec: PageSpec) -> bytes:
    ops = b""
    if spec.image:
        ops += b"q %d 0 0 %d 0 0 cm /Im0 Do Q\n" % (PAGE_W, PAGE_H)
    if spec.text:
        y = PAGE_H - 20
        for line in spec.text.split("\n"):
            mode = b"3 Tr " if spec.invisible else b""
            ops += b"BT %s/F1 10 Tf 10 %d Td (%s) Tj ET\n" % (
                mode, y, line.encode("latin-1"),
            )
            y -= 14
    return ops


def build_pdf(pages: list[PageSpec]) -> bytes:
    """Assemble a complete PDF with a correct xref table.

    Objects: 1 catalog, 2 pages, 3 font, 4 image, then per page i:
    5+2i (page dict), 6+2i (content stream)."""
    kids = b" ".join(b"%d 0 R" % (5 + 2 * i) for i in range(len(pages)))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(pages)),
        _FONT,
        _GRAY_IMAGE,
    ]
    for i, spec in enumerate(pages):
        xobj = b"/XObject << /Im0 4 0 R >> " if spec.image else b""
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
            b"/Resources << /Font << /F1 3 0 R >> %s>> /Contents %d 0 R >>"
            % (PAGE_W, PAGE_H, xobj, 6 + 2 * i)
        )
        objects.append(_stream(_content(spec)))

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref_at)
    )
    return bytes(out)


DIGITAL_TEXT = "Rechnung Nr. 4711 vom 3. Januar 2025, Betrag EUR 99,50"


def digital_pdf(text: str = DIGITAL_TEXT) -> bytes:
    """Born-digital: visible text, zero images."""
    return build_pdf([PageSpec(text=text)])


def scan_pdf() -> bytes:
    """A raw scan: one full-page image, no text layer at all."""
    return build_pdf([PageSpec(image=True)])


def ocr_layered_pdf(text: str = "previous tesseract text layer") -> bytes:
    """A previously-OCRed scan: full-page image + invisible (Tr 3)
    text — MUST classify as scan (redo-ocr semantics)."""
    return build_pdf([PageSpec(text=text, invisible=True, image=True)])


def mixed_pdf(text: str = DIGITAL_TEXT) -> bytes:
    """Page 1 born-digital, page 2 a raw scan."""
    return build_pdf([PageSpec(text=text), PageSpec(image=True)])
