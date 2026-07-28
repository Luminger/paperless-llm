"""OCR pipeline — plain vision calls, deliberately OUTSIDE any agent
tool loop (DESIGN.md "OCR pipeline").

Flow: fetch original from paperless -> classify pages (born-digital vs
scan, app.pdfio) -> digital pages read their native text layer directly
-> scan pages render to PNG (pypdfium2) in batches of
<= max_images_per_request per completion -> concatenate markdown ->
similarity vs. existing paperless `content` -> cache.

A page counts as born-digital only when it has a real VISIBLE text
layer: invisible (Tr 3) text over a full-page image — a previously
OCRed scan, e.g. paperless's own tesseract layer — classifies as a
scan and gets the full VLM treatment (--redo-ocr semantics).
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

from pydantic_ai import Agent, BinaryContent
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import pdfio
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
    # Pages whose text came straight from the PDF's native text layer
    # (born-digital) — no VLM involved.
    native_pages: int = 0
    # Partial run (max_pages limit hit): pages/text cover only the head.
    truncated: bool = False
    total_pages: int | None = None
    # Paperless `content` at run time — kept so superseded OCR steps can
    # still show the diff they produced back then.
    previous_content: str = ""


def page_count(data: bytes, content_type: str) -> int:
    return pdfio.page_count(data, content_type)


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


def render_pages(
    data: bytes, content_type: str, dpi: int, indices: list[int],
    auto_rotate: bool = False,
) -> list[tuple[bytes, int]]:
    """Render the given 0-based pages as (png, applied_rotation) tuples
    — AUDIT BC-F3: batches render lazily so peak memory is ONE batch,
    not the whole document. Indices need not be contiguous (the
    born-digital gate can punch holes into the scan set).

    ``auto_rotate``: PDFium already honors PDF /Rotate metadata; this
    additionally detects raster content that is itself upside-down or
    sideways (raw scans) and re-renders upright."""
    out: list[tuple[bytes, int]] = []
    for i in indices:
        png = pdfio.render_page(data, content_type, i, dpi)
        rotation = 0
        if auto_rotate:
            rotation = detect_rotation(png)
            if rotation:
                png = pdfio.render_page(data, content_type, i, dpi, rotation=rotation)
        out.append((png, rotation))
    return out


def render_page_range(
    data: bytes, content_type: str, dpi: int, start: int, count: int,
    auto_rotate: bool = False,
) -> list[tuple[bytes, int]]:
    """Contiguous-range convenience over :func:`render_pages`."""
    total = page_count(data, content_type)
    return render_pages(
        data, content_type, dpi, list(range(start, min(total, start + count))),
        auto_rotate,
    )


def _span_label(nums: list[int]) -> str:
    """Human page label for a sorted list of 1-based page numbers:
    [2, 5, 6, 7] -> "2, 5-7"."""
    spans: list[list[int]] = []
    for n in nums:
        if spans and n == spans[-1][1] + 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return ", ".join(f"{a}-{b}" if a != b else f"{a}" for a, b in spans)


def _native_count(timings: list[dict] | None) -> int:
    """Born-digital page count recorded in a run's timing entries."""
    return sum(t.get("count", 0) for t in timings or [] if t.get("native"))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def texts_equivalent(a: str, b: str) -> bool:
    """Whitespace/case-insensitive equality — "nothing to change": a
    rewrite would alter formatting at most, never content."""
    return _normalize(a) == _normalize(b)


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
    force_vlm: bool = False,
    progress: ProgressFn | None = None,
) -> OcrOutcome:
    """OCR one document, using the cache unless ``force``.

    ``instructions`` (user-supplied, e.g. from the OCR gate's re-run
    action) are appended to the OCR system prompt; ``dpi`` overrides the
    profile's render DPI for this run; ``force_vlm`` disables the
    born-digital gate and sends every page to the vision model (the
    gate's escape hatch for misclassified documents)."""
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
    if not force and cached:
        return OcrOutcome(
            document_id=document_id,
            checksum=checksum,
            model=model.model_name,
            pages=list(cached.pages),
            text=cached.text,
            similarity=cached.similarity,
            from_cache=True,
            timings=list(cached.timings or []),
            native_pages=_native_count(cached.timings),
            truncated=bool(cached.truncated),
            total_pages=cached.total_pages,
            previous_content=doc.content,
        )

    # AUDIT BC-F3: pdfio is pure CPU — everything renders in worker
    # threads, and only ONE batch of PNGs is in memory at a time (a
    # 100-page scan at 150 DPI is hundreds of MB fully materialized).
    total = await asyncio.to_thread(page_count, data, content_type)
    n_pages = total if profile.max_pages <= 0 else min(total, profile.max_pages)
    truncated = n_pages < total
    effective_dpi = dpi or profile.render_dpi

    # Born-digital gate: pages with a real visible text layer skip the
    # VLM entirely — their native text IS the ground truth.
    page_texts: list[str | None] = [None] * n_pages
    native_idx: list[int] = []
    timings: list[dict] = []
    if profile.native_text and not force_vlm and pdfio.is_pdf(content_type):
        profiles = await asyncio.to_thread(pdfio.classify_pages, data, content_type)
        if any(p.digital for p in profiles[:n_pages]):
            texts = await asyncio.to_thread(pdfio.native_page_texts, data, content_type)
            for i in range(min(n_pages, len(profiles), len(texts))):
                # Classifier said digital but extraction came back empty:
                # trust nothing, fall through to the VLM.
                if profiles[i].digital and texts[i].strip():
                    page_texts[i] = texts[i]
                    native_idx.append(i)
    if native_idx:
        timings.append({
            "pages": _span_label([i + 1 for i in native_idx]),
            "native": True,
            "count": len(native_idx),
            "duration_s": 0.0,
        })

    prompt = base_prompt
    if instructions:
        prompt += f"\nAdditional instructions from the user (follow them):\n{instructions}\n"
    agent: Agent[None, str] = Agent(model, system_prompt=prompt, model_settings=model_settings)
    batch = max(1, profile.max_images_per_request)
    scan_idx = [i for i in range(n_pages) if page_texts[i] is None]
    batches = [scan_idx[i : i + batch] for i in range(0, len(scan_idx), batch)]
    total_batches = len(batches)
    done_scans = 0
    for indices in batches:
        chunk = await asyncio.to_thread(
            render_pages, data, content_type, effective_dpi, indices,
            profile.auto_rotate,
        )
        label = _span_label([i + 1 for i in indices])
        rotated = [indices[k] + 1 for k, (_png, rot) in enumerate(chunk) if rot]
        parts: list[str | BinaryContent] = [
            f"Transcribe page(s) {label} of {n_pages}."
        ]
        parts += [BinaryContent(data=png, media_type="image/png") for png, _rot in chunk]
        async with semaphore:
            result = await agent.run(parts)
        entry: dict = {"pages": label}
        if rotated:
            entry["rotated"] = rotated
        for message in result.new_messages():
            details = getattr(message, "provider_details", None)
            if isinstance(details, dict) and "pllm_timing" in details:
                entry.update(details["pllm_timing"])
        timings.append(entry)
        out = result.output.strip()
        done_scans += len(chunk)
        if progress is not None:
            # Live view: batch text travels with the snapshot (the final
            # result stores only the concatenated text).
            await progress({
                "total_pages": n_pages,
                "done_pages": len(native_idx) + done_scans,
                "total_batches": total_batches,
                "batches": [
                    *timings[:-1],
                    {**entry, "text": out},
                ][-8:],  # last 8 batches: bounded row size on huge docs
            })
        if len(chunk) > 1:
            split = re.split(r"\n-{3,}\n", out)
            if len(split) == len(chunk):
                for k, i in enumerate(indices):
                    page_texts[i] = split[k].strip()
            else:
                # The model didn't separate pages as instructed — keep
                # the blob on the batch's first page; the concatenated
                # text is what matters.
                page_texts[indices[0]] = out
                for i in indices[1:]:
                    page_texts[i] = ""
        else:
            page_texts[indices[0]] = out

    pages = [t if t is not None else "" for t in page_texts]
    text = "\n\n".join(p for p in pages if p.strip()).strip()
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
        # Native entries are bookkeeping, not LLM calls.
        llm_requests=sum(1 for t in timings if not t.get("native")),
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
        native_pages=len(native_idx),
        truncated=truncated,
        total_pages=total,
        previous_content=doc.content,
    )
