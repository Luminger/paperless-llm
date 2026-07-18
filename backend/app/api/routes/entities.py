"""Thin browse proxy over paperless for the UI (documents, taxonomy).

The frontend never talks to paperless directly — one auth domain, and
the proxy can enrich with app-side state later (e.g. "has open
proposals")."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_paperless
from app.api.schemas import (
    DocumentOut,
    DocumentSearchPage,
    EntityOut,
    InstructionsOut,
    InstructionsUpdate,
    MergeCandidateOut,
)
from app.db.models import EntityType, Session, SessionStatus
from app.db.session import get_session
from app.paperless import PaperlessClient
from app.paperless.taxonomy import TAXONOMY, TAXONOMY_TYPES
from app.services.entity_index import merge_candidates
from app.services.instructions import (
    ensure_inbox_defaults,
    get_map,
    set_instructions,
)

router = APIRouter(prefix="/api/entities", tags=["entities"])


@router.get("/documents")
async def list_documents(
    query: str | None = None,
    tag_id: int | None = None,
    correspondent_id: int | None = None,
    document_type_id: int | None = None,
    page: int = 1,
    page_size: int = 25,
    paperless: PaperlessClient = Depends(get_paperless),
) -> DocumentSearchPage:
    result = await paperless.search_documents(
        query=query,
        tag_ids=[tag_id] if tag_id else None,
        correspondent_id=correspondent_id,
        document_type_id=document_type_id,
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
    from app.services.jobs import ACTIVE_PHASES

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
