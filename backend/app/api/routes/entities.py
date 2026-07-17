"""Thin browse proxy over paperless for the UI (documents, taxonomy).

The frontend never talks to paperless directly — one auth domain, and
the proxy can enrich with app-side state later (e.g. "has open
proposals")."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_paperless
from app.api.schemas import MergeCandidateOut
from app.db.session import get_session
from app.paperless import PaperlessClient
from app.services.entity_index import merge_candidates

router = APIRouter(prefix="/api/entities", tags=["entities"])

TAXONOMY_TYPES = ("tag", "correspondent", "document_type")


@router.get("/documents")
async def list_documents(
    query: str | None = None,
    page: int = 1,
    page_size: int = 25,
    paperless: PaperlessClient = Depends(get_paperless),
):
    result = await paperless.search_documents(query=query, page=page, page_size=page_size)
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


@router.get("/tags")
async def list_tags(paperless: PaperlessClient = Depends(get_paperless)):
    return [t.model_dump() for t in await paperless.list_tags()]


@router.get("/correspondents")
async def list_correspondents(paperless: PaperlessClient = Depends(get_paperless)):
    return [c.model_dump() for c in await paperless.list_correspondents()]


@router.get("/document_types")
async def list_document_types(paperless: PaperlessClient = Depends(get_paperless)):
    return [d.model_dump() for d in await paperless.list_document_types()]


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
