"""OCR pipeline — plain vision calls, deliberately OUTSIDE any agent
tool loop (DESIGN.md "OCR pipeline").

Flow: fetch original from paperless -> render pages to PNG (PyMuPDF) ->
batches of <= max_images_per_request pages per completion -> concatenate
markdown -> similarity vs. existing paperless `content` -> cache.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache

import fitz  # PyMuPDF
from pydantic_ai import Agent, BinaryContent
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OcrResult
from app.llm.factory import ocr_model
from app.paperless import PaperlessClient

log = logging.getLogger(__name__)

# Live-progress callback: receives a snapshot after every batch —
# {total_pages, done_pages, total_batches, batches: [entry, ...]} where
# each entry carries the batch's page range, rotations, timing metrics
# and the returned text.
ProgressFn = Callable[[dict], Awaitable[None]]

OCR_PROMPT = """\
You are a precise OCR engine. Transcribe the document page image(s) into
clean Markdown, exactly as written.

Rules:
- Preserve the original language of the document. Do NOT translate.
- Preserve reading order; use Markdown headings/lists/tables where the
  layout clearly implies them.
- Transcribe numbers, dates, amounts, IBANs and reference numbers with
  utmost care - never guess a digit.
- Mark truly illegible passages as `[illegible]`. Never invent text.
- Output ONLY the transcription. No commentary, no code fences.
- If more than one page image is provided, separate the transcriptions
  with a line containing exactly `---`.
"""


@dataclass
class OcrOutcome:
    document_id: int
    checksum: str
    model: str
    pages: list[str]
    text: str
    similarity: float | None  # vs. paperless `content` at run time; None if no content
    from_cache: bool
    timings: list[dict] | None = None  # per-batch LLM call metrics
    # Partial run (max_pages limit hit): pages/text cover only the head.
    truncated: bool = False
    total_pages: int | None = None
    # Paperless `content` at run time — kept so superseded OCR steps can
    # still show the diff they produced back then.
    previous_content: str = ""


def page_count(data: bytes, content_type: str) -> int:
    doc = fitz.open(stream=data, filetype="pdf" if "pdf" in content_type else None)
    try:
        return doc.page_count
    finally:
        doc.close()


@lru_cache(maxsize=1)
def _tesseract_available() -> bool:
    ok = shutil.which("tesseract") is not None
    if not ok:
        log.info("tesseract not found — OCR auto-rotate disabled")
    return ok


_OSD_ROTATE = re.compile(r"^Rotate: (\d+)", re.MULTILINE)


def detect_rotation(png: bytes) -> int:
    """Degrees the page must be rotated CLOCKWISE to read upright
    (0/90/180/270) via tesseract orientation detection; 0 when
    unavailable or undecidable. Blocking — call from a worker thread."""
    if not _tesseract_available():
        return 0
    try:
        proc = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", "0"],
            input=png, capture_output=True, timeout=30,
        )
        m = _OSD_ROTATE.search(proc.stdout.decode(errors="replace"))
        return int(m.group(1)) % 360 if m else 0
    except Exception:  # noqa: BLE001 — orientation is best-effort
        return 0


def render_page_range(
    data: bytes, content_type: str, dpi: int, start: int, count: int,
    auto_rotate: bool = False,
) -> list[tuple[bytes, int]]:
    """Render pages [start, start+count) as (png, applied_rotation)
    tuples — AUDIT BC-F3: batches render lazily so peak memory is ONE
    batch, not the whole document.

    ``auto_rotate``: PyMuPDF already honors PDF /Rotate metadata; this
    additionally detects raster content that is itself upside-down or
    sideways (raw scans) and re-renders upright."""
    doc = fitz.open(stream=data, filetype="pdf" if "pdf" in content_type else None)
    try:
        zoom = dpi / 72.0
        end = min(doc.page_count, start + count)
        out: list[tuple[bytes, int]] = []
        for i in range(start, end):
            png = doc[i].get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png")
            rotation = 0
            if auto_rotate:
                rotation = detect_rotation(png)
                if rotation:
                    png = (
                        doc[i]
                        .get_pixmap(matrix=fitz.Matrix(zoom, zoom).prerotate(rotation))
                        .tobytes("png")
                    )
            out.append((png, rotation))
        return out
    finally:
        doc.close()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def content_similarity(a: str, b: str) -> float:
    """0..1 similarity between two OCR text bodies (whitespace/case
    insensitive token-set ratio — robust to layout reflow)."""
    from rapidfuzz import fuzz

    na, nb = _normalize(a), _normalize(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return fuzz.token_set_ratio(na, nb) / 100.0


async def run_ocr(
    paperless: PaperlessClient,
    db: AsyncSession,
    document_id: int,
    *,
    force: bool = False,
    instructions: str | None = None,
    dpi: int | None = None,
    progress: ProgressFn | None = None,
) -> OcrOutcome:
    """OCR one document, using the cache unless ``force``.

    ``instructions`` (user-supplied, e.g. from the OCR gate's re-run
    action) are appended to the OCR system prompt; ``dpi`` overrides the
    profile's render DPI for this run."""
    model, model_settings, profile, semaphore = ocr_model()
    doc = await paperless.get_document(document_id)
    data, content_type = await paperless.download_original(document_id)
    checksum = hashlib.sha256(data).hexdigest()

    # The OCR prompt is user-tunable (Settings): base override + user
    # addition. Tweaks invalidate the cache via a fingerprint.
    from app.services.prefs import get_prefs

    prefs = await get_prefs(db)
    from app.services.prefs import with_owner_addition

    base_prompt = with_owner_addition(
        prefs.get("ocr_prompt_base", "").strip() or OCR_PROMPT,
        prefs.get("ocr_prompt_addition", ""),
    )
    fingerprint = hashlib.sha256(base_prompt.encode()).hexdigest()[:16]

    cached = await db.scalar(
        select(OcrResult).where(
            OcrResult.document_id == document_id,
            OcrResult.checksum == checksum,
            OcrResult.model == model.model_name,
            OcrResult.prompt_version == profile.prompt_version,
            OcrResult.prompt_fingerprint == fingerprint,
        )
    )
    if not force:
        if cached:
            return OcrOutcome(
                document_id=document_id,
                checksum=checksum,
                model=model.model_name,
                pages=list(cached.pages),
                text=cached.text,
                similarity=cached.similarity,
                from_cache=True,
                timings=list(cached.timings or []),
                truncated=bool(cached.truncated),
                total_pages=cached.total_pages,
                previous_content=doc.content,
            )

    # AUDIT BC-F3: PyMuPDF is pure CPU — everything renders in worker
    # threads, and only ONE batch of PNGs is in memory at a time (a
    # 100-page scan at 150 DPI is hundreds of MB fully materialized).
    total = await asyncio.to_thread(page_count, data, content_type)
    n_pages = total if profile.max_pages <= 0 else min(total, profile.max_pages)
    truncated = n_pages < total
    effective_dpi = dpi or profile.render_dpi

    prompt = base_prompt
    if instructions:
        prompt += f"\nAdditional instructions from the user (follow them):\n{instructions}\n"
    agent: Agent[None, str] = Agent(model, system_prompt=prompt, model_settings=model_settings)
    batch = max(1, profile.max_images_per_request)
    pages: list[str] = []
    timings: list[dict] = []
    total_batches = -(-n_pages // batch)
    for i in range(0, n_pages, batch):
        chunk = await asyncio.to_thread(
            render_page_range, data, content_type, effective_dpi, i,
            min(batch, n_pages - i), profile.auto_rotate,
        )
        rotated = [i + 1 + k for k, (_png, rot) in enumerate(chunk) if rot]
        parts: list[str | BinaryContent] = [
            f"Transcribe page(s) {i + 1}-{i + len(chunk)} of {n_pages}."
        ]
        parts += [BinaryContent(data=png, media_type="image/png") for png, _rot in chunk]
        async with semaphore:
            result = await agent.run(parts)
        entry: dict = {"pages": f"{i + 1}-{i + len(chunk)}"}
        if rotated:
            entry["rotated"] = rotated
        for message in result.new_messages():
            details = getattr(message, "provider_details", None)
            if isinstance(details, dict) and "pllm_timing" in details:
                entry.update(details["pllm_timing"])
        timings.append(entry)
        out = result.output.strip()
        if progress is not None:
            # Live view: batch text travels with the snapshot (the final
            # result stores only the concatenated text).
            await progress({
                "total_pages": n_pages,
                "done_pages": min(i + len(chunk), n_pages),
                "total_batches": total_batches,
                "batches": [
                    *timings[:-1],
                    {**entry, "text": out},
                ][-8:],  # last 8 batches: bounded row size on huge docs
            })
        if len(chunk) > 1:
            split = re.split(r"\n-{3,}\n", out)
            # If the model didn't separate pages as instructed, keep the
            # blob as one page entry — the concatenated text is what matters.
            pages += [s.strip() for s in split] if len(split) == len(chunk) else [out]
        else:
            pages.append(out)

    text = "\n\n".join(pages).strip()
    # AUDIT BC-F17: similarity vs. the FULL existing content is
    # meaningless for a partial transcription — report unknown instead
    # of "artificially low".
    similarity = (
        content_similarity(text, doc.content)
        if doc.content.strip() and not truncated
        else None
    )

    # Upsert: a force re-run must update the existing cache row, not
    # violate the unique key.
    if cached:
        cached.pages = pages
        cached.text = text
        cached.similarity = similarity
        cached.timings = timings
        cached.truncated = truncated
        cached.total_pages = total
        await db.commit()
    else:
        db.add(
            OcrResult(
                document_id=document_id,
                checksum=checksum,
                model=model.model_name,
                prompt_version=profile.prompt_version,
                prompt_fingerprint=fingerprint,
                pages=pages,
                text=text,
                similarity=similarity,
                timings=timings,
                truncated=truncated,
                total_pages=total,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            # AUDIT BC-F9: two workers OCRed the same document
            # concurrently — the loser updates the winner's row instead
            # of failing the step (and re-running the whole OCR).
            await db.rollback()
            row = await db.scalar(
                select(OcrResult).where(
                    OcrResult.document_id == document_id,
                    OcrResult.checksum == checksum,
                    OcrResult.model == model.model_name,
                    OcrResult.prompt_version == profile.prompt_version,
                    OcrResult.prompt_fingerprint == fingerprint,
                )
            )
            if row is not None:
                row.pages = pages
                row.text = text
                row.similarity = similarity
                row.timings = timings
                row.truncated = truncated
                row.total_pages = total
                await db.commit()

    # Counters AFTER the upsert settles (reinspection): they used to run
    # before the insert commit, so the BC-F9 loser's rollback silently
    # discarded the deltas — a full real OCR run went uncounted.
    from app.services.counters import increment

    await increment(
        db,
        ocr_runs=1,
        ocr_pages=len(pages),
        llm_requests=len(timings),
        llm_input_tokens=sum(t.get("input_tokens") or 0 for t in timings),
        llm_output_tokens=sum(t.get("output_tokens") or 0 for t in timings),
    )
    await db.commit()

    return OcrOutcome(
        document_id=document_id,
        checksum=checksum,
        model=model.model_name,
        pages=pages,
        text=text,
        similarity=similarity,
        from_cache=False,
        timings=timings,
        truncated=truncated,
        total_pages=total,
        previous_content=doc.content,
    )
