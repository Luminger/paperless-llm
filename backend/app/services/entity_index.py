"""Entity name index: string distance always, embeddings when a local
embedding endpoint is configured (``[llm.embeddings]``).

Powers the ``find_similar_entities`` agent tool and the deterministic
merge-candidate pre-pass: candidate *finding* is cheap and mechanical
(cosine + fuzzy ratio); the LLM only *adjudicates* whether candidates
are truly the same thing.

Embedding vectors are cached in the ``entity_embeddings`` table keyed by
(entity_type, entity_id) and refreshed when names change. Taxonomies are
small (hundreds, not millions), so similarity is brute-force cosine in
process — no vector extension needed here (document-chunk RAG in M5 is a
different story).
"""

from __future__ import annotations

import math
from typing import Any

import httpx
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import EntityEmbedding
from app.paperless import PaperlessClient
from app.paperless.taxonomy import TAXONOMY

# Candidate thresholds: either metric qualifies. String ratio catches
# "Kraxi"/"Kraxi GmbH"; embeddings catch semantic twins with different
# surface forms ("Finanzamt München"/"Tax Office Munich").
STRING_THRESHOLD = 0.82
SEMANTIC_THRESHOLD = 0.80


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed via the configured local OpenAI-compatible endpoint (TEI)."""
    prof = get_settings().llm.embeddings
    out: list[list[float]] = []
    async with httpx.AsyncClient(timeout=60) as client:
        # TEI happily takes batches; keep them bounded.
        for i in range(0, len(texts), 64):
            batch = texts[i : i + 64]
            payload: dict[str, Any] = {"model": prof.model, "input": batch}
            if prof.dimensions:
                payload["dimensions"] = prof.dimensions
            resp = await client.post(
                f"{prof.base_url.rstrip('/')}/embeddings",
                json=payload,
                headers={"Authorization": f"Bearer {prof.api_key}"},
            )
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            out += [d["embedding"] for d in data]
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def string_similarity(a: str, b: str) -> float:
    """0..1: token-sort ratio is robust against word order and suffixes
    like legal forms ("GmbH")."""
    return (
        max(
            fuzz.token_sort_ratio(a.lower(), b.lower()),
            fuzz.partial_ratio(a.lower(), b.lower()),
        )
        / 100.0
    )


async def _ensure_vectors(
    db: AsyncSession, entity_type: str, entities: list[Any]
) -> dict[int, list[float]]:
    """Return id->vector for all entities, embedding only new/renamed
    ones and pruning rows for entities that no longer exist."""
    rows = {
        r.entity_id: r
        for r in (
            await db.scalars(
                select(EntityEmbedding).where(EntityEmbedding.entity_type == entity_type)
            )
        ).all()
    }
    live_ids = {e.id for e in entities}
    for entity_id, row in list(rows.items()):
        if entity_id not in live_ids:
            await db.delete(row)
            del rows[entity_id]

    stale = [e for e in entities if e.id not in rows or rows[e.id].name != e.name]
    if stale:
        vectors = await embed_texts([e.name for e in stale])
        for e, vec in zip(stale, vectors, strict=True):
            if e.id in rows:
                rows[e.id].name = e.name
                rows[e.id].vector = vec
            else:
                row = EntityEmbedding(
                    entity_type=entity_type, entity_id=e.id, name=e.name, vector=vec
                )
                db.add(row)
                rows[e.id] = row
    await db.commit()
    return {eid: r.vector for eid, r in rows.items() if r.vector}


async def _list_entities(paperless: PaperlessClient, entity_type: str) -> list[Any]:
    return await TAXONOMY[entity_type].list(paperless)


async def find_similar(
    db: AsyncSession,
    paperless: PaperlessClient,
    entity_type: str,
    name: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Entities whose names resemble ``name``, scored 0..1 (max of string
    and semantic similarity)."""
    entities = await _list_entities(paperless, entity_type)
    if not entities:
        return []

    scores: dict[int, dict[str, Any]] = {
        e.id: {
            "id": e.id,
            "name": e.name,
            "document_count": e.document_count,
            "string_score": round(string_similarity(name, e.name), 3),
            "semantic_score": None,
        }
        for e in entities
    }

    if get_settings().llm.embeddings.enabled:
        vectors = await _ensure_vectors(db, entity_type, entities)
        (query_vec,) = await embed_texts([name])
        for eid, vec in vectors.items():
            scores[eid]["semantic_score"] = round(_cosine(query_vec, vec), 3)

    ranked = sorted(
        scores.values(),
        key=lambda s: max(s["string_score"], s["semantic_score"] or 0.0),
        reverse=True,
    )
    for s in ranked:
        s["similarity"] = max(s["string_score"], s["semantic_score"] or 0.0)
    return ranked[:top_k]


async def merge_candidates(
    db: AsyncSession, paperless: PaperlessClient, entity_type: str
) -> list[dict[str, Any]]:
    """Deterministic duplicate pre-pass: all pairs above either
    threshold, sorted by score. The larger/better-connected entity is
    presented as the merge target."""
    entities = await _list_entities(paperless, entity_type)
    vectors: dict[int, list[float]] = {}
    if get_settings().llm.embeddings.enabled and entities:
        vectors = await _ensure_vectors(db, entity_type, entities)

    pairs: list[dict[str, Any]] = []
    for i, a in enumerate(entities):
        for b in entities[i + 1 :]:
            s_str = string_similarity(a.name, b.name)
            s_sem = (
                _cosine(vectors[a.id], vectors[b.id])
                if a.id in vectors and b.id in vectors
                else None
            )
            if s_str < STRING_THRESHOLD and (s_sem is None or s_sem < SEMANTIC_THRESHOLD):
                continue
            # Target = the entity more documents already point at.
            target, source = (
                (a, b) if (a.document_count or 0) >= (b.document_count or 0) else (b, a)
            )
            pairs.append(
                {
                    "entity_type": entity_type,
                    "source": {"id": source.id, "name": source.name,
                               "document_count": source.document_count},
                    "target": {"id": target.id, "name": target.name,
                               "document_count": target.document_count},
                    "string_score": round(s_str, 3),
                    "semantic_score": round(s_sem, 3) if s_sem is not None else None,
                }
            )
    pairs.sort(
        key=lambda p: max(p["string_score"], p["semantic_score"] or 0.0), reverse=True
    )
    return pairs

