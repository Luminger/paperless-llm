"""Thin browse proxy over paperless for the UI (documents, taxonomy).

The frontend never talks to paperless directly — one auth domain, and
the proxy can enrich with app-side state later (e.g. "has open
proposals")."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import pdfio
from app.api.deps import get_paperless
from app.api.schemas import (
    CustomFieldOut,
    DocumentHistoryOut,
    DocumentOut,
    DocumentSearchPage,
    EntityOut,
    InstructionsOut,
    InstructionsUpdate,
    MergeCandidateOut,
)
from app.db.models import AppliedChange, EntityType, Proposal, Session, SessionStatus
from app.db.session import get_session
from app.paperless import PaperlessClient
from app.paperless.taxonomy import TAXONOMY, TAXONOMY_TYPES
from app.services.entity_index import merge_candidates
from app.services.instructions import (
    ensure_inbox_defaults,
    get_map,
    set_instructions,
)
from app.services.jobs import ACTIVE_PHASES

router = APIRouter(prefix="/api/entities", tags=["entities"])


def _id_list(v: str | None) -> list[int] | None:
    """Comma-separated ids from the URL ("1,5,9") — empty means None.
    AUDIT API-F9: malformed input is the CLIENT's error (422), never a
    500."""
    if not v:
        return None
    try:
        return [int(part) for part in v.split(",") if part.strip()]
    except ValueError as e:
        raise HTTPException(422, f"invalid id list {v!r}") from e


@router.get("/documents")
async def list_documents(
    query: str | None = None,
    tag_ids: str | None = None,
    correspondent_ids: str | None = None,
    document_type_ids: str | None = None,
    page: int = 1,
    page_size: int = 25,
    paperless: PaperlessClient = Depends(get_paperless),
) -> DocumentSearchPage:
    """Browse filters are multiselects with ANY-of semantics — ids come
    comma-separated per taxonomy type."""
    # AUDIT API-F10: clamp what we proxy — page_size=100000 would make
    # paperless serialize its whole archive per request.
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    result = await paperless.search_documents(
        query=query,
        tags_any=_id_list(tag_ids),
        correspondent_ids=_id_list(correspondent_ids),
        document_type_ids=_id_list(document_type_ids),
        page=page,
        page_size=page_size,
    )
    return DocumentSearchPage(
        count=result.count,
        page_size=page_size,
        all=result.all,
        results=[
            DocumentOut(**d.model_dump(exclude={"content"}))
            for d in result.results
        ],
    )


@router.get("/inbox")
async def inbox_backlog(
    limit: int = 8,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> DocumentSearchPage:
    """The inbox WITHOUT documents that already have an active session
    — the dashboard's "waiting to be looked at" list. ``count`` is the
    full backlog; ``results`` are the first ``limit`` entries."""
    inbox_tags = [t.id for t in await paperless.list_tags() if t.is_inbox_tag]
    if not inbox_tags:
        return DocumentSearchPage(count=0, results=[])
    page = await paperless.search_documents(tag_ids=inbox_tags, page_size=100)
    docs = list(page.results)
    active = set(
        (
            await db.scalars(
                select(Session.entity_id).where(
                    Session.entity_type == EntityType.document,
                    Session.entity_id.in_([d.id for d in docs] or [0]),
                    Session.phase.in_(ACTIVE_PHASES),
                    Session.status != SessionStatus.failed,
                )
            )
        ).all()
    )
    waiting = [d for d in docs if d.id not in active]
    return DocumentSearchPage(
        count=len(waiting),
        results=[
            DocumentOut(**d.model_dump(exclude={"content"}))
            for d in waiting[:limit]
        ],
    )


@router.get("/documents/{doc_id}")
async def get_document(
    doc_id: int, paperless: PaperlessClient = Depends(get_paperless)
) -> DocumentOut:
    doc = await paperless.get_document(doc_id)
    return DocumentOut(**doc.model_dump(include=set(DocumentOut.model_fields)))


# In-memory cache of the last few archived PDFs — paging through a
# preview must not re-download the document per page.
_preview_cache: dict[int, tuple[float, bytes, str]] = {}
_PREVIEW_CACHE_MAX = 4
_PREVIEW_CACHE_TTL = 300.0  # re-archived documents go stale within 5 min


async def _archived(paperless: PaperlessClient, doc_id: int) -> tuple[bytes, str]:
    # AUDIT API-F2: the cache is shared across users — authorize EVERY
    # request with the CALLER's paperless client before touching it. A
    # user whose token 403s/404s on the document never sees cached
    # bytes another user's preview pulled in.
    await paperless.get_document(doc_id)
    now = time.monotonic()
    hit = _preview_cache.get(doc_id)
    if hit is not None and now - hit[0] <= _PREVIEW_CACHE_TTL:
        return hit[1], hit[2]
    data, content_type = await paperless.download_archived(doc_id)
    _preview_cache[doc_id] = (now, data, content_type)
    while len(_preview_cache) > _PREVIEW_CACHE_MAX:
        _preview_cache.pop(next(iter(_preview_cache)))
    return data, content_type


def _render_single_page(
    data: bytes, content_type: str, page: int, dpi: int
) -> bytes | None:
    if page < 1 or page > pdfio.page_count(data, content_type):
        return None
    return pdfio.render_page(data, content_type, page - 1, dpi)


@router.get("/documents/{doc_id}/preview")
async def preview_info(
    doc_id: int, paperless: PaperlessClient = Depends(get_paperless)
) -> dict:
    """Page count of the archived rendition — drives the pager."""
    data, content_type = await _archived(paperless, doc_id)
    pages = await asyncio.to_thread(pdfio.page_count, data, content_type)
    return {"pages": pages}


@router.get("/documents/{doc_id}/preview/{page}")
async def preview_page(
    doc_id: int,
    page: int,
    dpi: int = 130,
    paperless: PaperlessClient = Depends(get_paperless),
) -> Response:
    """One page of the archived rendition as PNG (1-based)."""
    data, content_type = await _archived(paperless, doc_id)
    # Render OFF the event loop (pdfio is pure CPU), and only the
    # requested page — not every page up to it.
    png = await asyncio.to_thread(
        _render_single_page, data, content_type, page, min(max(dpi, 50), 220)
    )
    if png is None:
        raise HTTPException(404, "page out of range")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/documents/{doc_id}/history")
async def document_history(
    doc_id: int,
    db: AsyncSession = Depends(get_session),
) -> list[DocumentHistoryOut]:
    """Every change this app applied to the document — journaled,
    attributed, linked to the session that produced it."""
    rows = (
        await db.execute(
            select(Proposal, AppliedChange, Session.title)
            .join(AppliedChange, AppliedChange.proposal_id == Proposal.id)
            .outerjoin(Session, Session.id == Proposal.session_id)
            .where(
                Proposal.entity_type == EntityType.document,
                Proposal.entity_id == doc_id,
            )
            .order_by(AppliedChange.applied_at.desc())
        )
    ).all()
    out: list[DocumentHistoryOut] = []
    for proposal, change, session_title in rows:
        payload = proposal.user_payload or proposal.agent_payload or {}
        fields = sorted(
            k for k, v in payload.items()
            if k not in ("kind", "document_id", "entity_type", "entity_id")
            and v is not None
        )
        out.append(
            DocumentHistoryOut(
                proposal_id=proposal.id,
                session_id=proposal.session_id,
                session_title=session_title or "",
                kind=str(proposal.kind),
                fields=fields,
                applied_at=change.applied_at,
                applied_by=change.actor,
                edited=proposal.user_payload is not None,
                reverted=change.reverted_at is not None,
            )
        )
    return out


@router.get("/documents/{doc_id}/thumb")
async def get_thumbnail(doc_id: int, paperless: PaperlessClient = Depends(get_paperless)):
    content, media_type = await paperless.get_thumbnail(doc_id)
    return Response(content=content, media_type=media_type)


def _entity_out(e, instructions: str = "") -> EntityOut:
    return EntityOut(
        **e.model_dump(include=set(EntityOut.model_fields) - {"instructions"}),
        instructions=instructions,
    )


def _with_instructions(entities: list, instr: dict[int, str]) -> list[EntityOut]:
    return [_entity_out(e, instr.get(e.id, "")) for e in entities]


@router.get("/tags")
async def list_tags(
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> list[EntityOut]:
    tags = await paperless.list_tags()
    # First sight of an inbox tag seeds its default instruction.
    await ensure_inbox_defaults(db, tags)
    return _with_instructions(tags, await get_map(db, "tag"))


@router.get("/correspondents")
async def list_correspondents(
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> list[EntityOut]:
    return _with_instructions(
        await paperless.list_correspondents(), await get_map(db, "correspondent")
    )


@router.get("/document_types")
async def list_document_types(
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> list[EntityOut]:
    return _with_instructions(
        await paperless.list_document_types(), await get_map(db, "document_type")
    )


@router.get("/storage_paths")
async def list_storage_paths(
    paperless: PaperlessClient = Depends(get_paperless),
) -> list[EntityOut]:
    return [_entity_out(s) for s in await paperless.list_storage_paths()]


@router.get("/custom_fields")
async def list_custom_fields(
    paperless: PaperlessClient = Depends(get_paperless),
) -> list[CustomFieldOut]:
    """The custom-field registry: names, data types, select options.
    Read-only — fields are defined in paperless; here they inform the
    proposal editor's typed widgets and the document facts."""
    return [
        CustomFieldOut(
            id=f.id,
            name=f.name,
            data_type=f.data_type,
            select_options=[
                o
                for o in ((f.extra_data or {}).get("select_options") or [])
                if isinstance(o, dict) and o.get("id") is not None
            ],
        )
        for f in await paperless.list_custom_fields()
    ]


@router.get("/{entity_type}/merge-candidates")
async def get_merge_candidates(
    entity_type: str,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> list[MergeCandidateOut]:
    """Deterministic duplicate pre-pass over one taxonomy: pairs whose
    names are close by string distance or embedding cosine. The agent
    (or the human) adjudicates; this only finds candidates."""
    if entity_type not in TAXONOMY_TYPES:
        raise HTTPException(422, f"no merge candidates for {entity_type!r}")
    pairs = await merge_candidates(db, paperless, entity_type)
    return [MergeCandidateOut.model_validate(p) for p in pairs]


@router.get("/{entity_type}/{entity_id}")
async def get_entity(
    entity_type: str,
    entity_id: int,
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
) -> EntityOut:
    """Generic taxonomy entity detail (documents have their own route)."""
    spec = TAXONOMY.get(entity_type)
    if spec is None:
        raise HTTPException(422, f"unknown entity type {entity_type!r}")
    entity = await spec.get(paperless, entity_id)
    if entity_type == "tag":
        await ensure_inbox_defaults(db, [entity])
    instr = await get_map(db, entity_type)
    return _entity_out(entity, instr.get(entity_id, ""))


@router.put("/{entity_type}/{entity_id}/instructions")
async def put_instructions(
    entity_type: str,
    entity_id: int,
    body: InstructionsUpdate,
    db: AsyncSession = Depends(get_session),
) -> InstructionsOut:
    """Set the app-local agent instructions for a taxonomy entity.
    Clearing stores an empty row — seeded defaults never come back."""
    if entity_type not in TAXONOMY_TYPES:
        raise HTTPException(422, f"no instructions for {entity_type!r}")
    await set_instructions(db, entity_type, entity_id, body.instructions)
    return InstructionsOut(
        entity_type=entity_type, entity_id=entity_id,
        instructions=body.instructions,
    )
