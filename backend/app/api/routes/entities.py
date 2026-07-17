"""Thin browse proxy over paperless for the UI (documents, taxonomy).

The frontend never talks to paperless directly — one auth domain, and
the proxy can enrich with app-side state later (e.g. "has open
proposals")."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_paperless
from app.api.schemas import InstructionsUpdate, MergeCandidateOut
from app.db.session import get_session
from app.paperless import PaperlessClient
from app.services.entity_index import merge_candidates
from app.services.instructions import (
    ensure_inbox_defaults,
    get_map,
    set_instructions,
)

router = APIRouter(prefix="/api/entities", tags=["entities"])

TAXONOMY_TYPES = ("tag", "correspondent", "document_type")


@router.get("/documents")
async def list_documents(
    query: str | None = None,
    tag_id: int | None = None,
    correspondent_id: int | None = None,
    document_type_id: int | None = None,
    page: int = 1,
    page_size: int = 25,
    paperless: PaperlessClient = Depends(get_paperless),
):
    result = await paperless.search_documents(
        query=query,
        tag_ids=[tag_id] if tag_id else None,
        correspondent_id=correspondent_id,
        document_type_id=document_type_id,
        page=page,
        page_size=page_size,
    )
    return result.model_dump(exclude={"results": {"__all__": {"content"}}})


@router.get("/documents/{doc_id}")
async def get_document(doc_id: int, paperless: PaperlessClient = Depends(get_paperless)):
    return (await paperless.get_document(doc_id)).model_dump()


@router.get("/documents/{doc_id}/thumb")
async def get_thumbnail(doc_id: int, paperless: PaperlessClient = Depends(get_paperless)):
    resp = await paperless._request("GET", f"/api/documents/{doc_id}/thumb/")
    return Response(
        content=resp.content, media_type=resp.headers.get("content-type", "image/webp")
    )


def _with_instructions(entities: list, instr: dict[int, str]) -> list[dict]:
    return [e.model_dump() | {"instructions": instr.get(e.id, "")} for e in entities]


@router.get("/tags")
async def list_tags(
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
):
    tags = await paperless.list_tags()
    # First sight of an inbox tag seeds its default instruction.
    await ensure_inbox_defaults(db, tags)
    return _with_instructions(tags, await get_map(db, "tag"))


@router.get("/correspondents")
async def list_correspondents(
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
):
    return _with_instructions(
        await paperless.list_correspondents(), await get_map(db, "correspondent")
    )


@router.get("/document_types")
async def list_document_types(
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
):
    return _with_instructions(
        await paperless.list_document_types(), await get_map(db, "document_type")
    )


@router.get("/storage_paths")
async def list_storage_paths(paperless: PaperlessClient = Depends(get_paperless)):
    return [s.model_dump() for s in await paperless.list_storage_paths()]


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
):
    """Generic taxonomy entity detail (documents have their own route)."""
    if entity_type not in TAXONOMY_TYPES and entity_type != "storage_path":
        raise HTTPException(422, f"unknown entity type {entity_type!r}")
    getter = {
        "tag": paperless.get_tag,
        "correspondent": paperless.get_correspondent,
        "document_type": paperless.get_document_type,
    }.get(entity_type)
    if getter is None:
        raise HTTPException(422, f"no detail for {entity_type!r}")
    entity = await getter(entity_id)
    if entity_type == "tag":
        await ensure_inbox_defaults(db, [entity])
    instr = await get_map(db, entity_type)
    return entity.model_dump() | {"instructions": instr.get(entity_id, "")}


@router.put("/{entity_type}/{entity_id}/instructions")
async def put_instructions(
    entity_type: str,
    entity_id: int,
    body: InstructionsUpdate,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Set the app-local agent instructions for a taxonomy entity.
    Clearing stores an empty row — seeded defaults never come back."""
    if entity_type not in TAXONOMY_TYPES:
        raise HTTPException(422, f"no instructions for {entity_type!r}")
    await set_instructions(db, entity_type, entity_id, body.instructions)
    return {"entity_type": entity_type, "entity_id": entity_id,
            "instructions": body.instructions}
