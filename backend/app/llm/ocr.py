"""OCR pipeline — plain vision calls, deliberately OUTSIDE any agent
tool loop (DESIGN.md "OCR pipeline").

Flow: fetch original from paperless -> render pages to PNG (PyMuPDF) ->
batches of <= max_images_per_request pages per completion -> concatenate
markdown -> similarity vs. existing paperless `content` -> cache.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import fitz  # PyMuPDF
from pydantic_ai import Agent, BinaryContent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OcrResult
from app.llm.factory import ocr_model
from app.paperless import PaperlessClient

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
    # Paperless `content` at run time — kept so superseded OCR steps can
    # still show the diff they produced back then.
    previous_content: str = ""


def render_pages(data: bytes, content_type: str, dpi: int, max_pages: int = 0) -> list[bytes]:
    """Render an original document (PDF or image) to PNG page images."""
    filetype = "pdf" if "pdf" in content_type else None
    doc = fitz.open(stream=data, filetype=filetype)
    try:
        n = doc.page_count if max_pages <= 0 else min(doc.page_count, max_pages)
        zoom = dpi / 72.0
        return [
            doc[i].get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png") for i in range(n)
        ]
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
) -> OcrOutcome:
    """OCR one document, using the cache unless ``force``.

    ``instructions`` (user-supplied, e.g. from the OCR gate's re-run
    action) are appended to the OCR system prompt; ``dpi`` overrides the
    profile's render DPI for this run."""
    model, model_settings, profile, semaphore = ocr_model()
    doc = await paperless.get_document(document_id)
    data, content_type = await paperless.download_original(document_id)
    checksum = hashlib.sha256(data).hexdigest()

    cached = await db.scalar(
        select(OcrResult).where(
            OcrResult.document_id == document_id,
            OcrResult.checksum == checksum,
            OcrResult.model == model.model_name,
            OcrResult.prompt_version == profile.prompt_version,
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
                previous_content=doc.content,
            )

    images = render_pages(data, content_type, dpi or profile.render_dpi, profile.max_pages)

    prompt = OCR_PROMPT
    if instructions:
        prompt += f"\nAdditional instructions from the user (follow them):\n{instructions}\n"
    agent: Agent[None, str] = Agent(model, system_prompt=prompt, model_settings=model_settings)
    batch = max(1, profile.max_images_per_request)
    pages: list[str] = []
    timings: list[dict] = []
    for i in range(0, len(images), batch):
        chunk = images[i : i + batch]
        parts: list[str | BinaryContent] = [
            f"Transcribe page(s) {i + 1}-{i + len(chunk)} of {len(images)}."
        ]
        parts += [BinaryContent(data=png, media_type="image/png") for png in chunk]
        async with semaphore:
            result = await agent.run(parts)
        for message in result.new_messages():
            details = getattr(message, "provider_details", None)
            if isinstance(details, dict) and "pllm_timing" in details:
                timings.append(
                    {"pages": f"{i + 1}-{i + len(chunk)}", **details["pllm_timing"]}
                )
        out = result.output.strip()
        if len(chunk) > 1:
            split = re.split(r"\n-{3,}\n", out)
            # If the model didn't separate pages as instructed, keep the
            # blob as one page entry — the concatenated text is what matters.
            pages += [s.strip() for s in split] if len(split) == len(chunk) else [out]
        else:
            pages.append(out)

    text = "\n\n".join(pages).strip()
    similarity = content_similarity(text, doc.content) if doc.content.strip() else None

    # Upsert: a force re-run must update the existing cache row, not
    # violate the unique key.
    from app.services.counters import increment

    await increment(
        db,
        ocr_runs=1,
        ocr_pages=len(pages),
        llm_requests=len(timings),
        llm_input_tokens=sum(t.get("input_tokens") or 0 for t in timings),
        llm_output_tokens=sum(t.get("output_tokens") or 0 for t in timings),
    )
    if cached:
        cached.pages = pages
        cached.text = text
        cached.similarity = similarity
        cached.timings = timings
    else:
        db.add(
            OcrResult(
                document_id=document_id,
                checksum=checksum,
                model=model.model_name,
                prompt_version=profile.prompt_version,
                pages=pages,
                text=text,
                similarity=similarity,
                timings=timings,
            )
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
        previous_content=doc.content,
    )
