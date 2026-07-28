"""Entity index: string-distance always; embeddings via a respx-mocked
local TEI endpoint when configured."""

from __future__ import annotations

import asyncio
import json

import pytest
import respx
from httpx import Response

from app.config import reset_settings_cache
from app.services.entity_index import find_similar, merge_candidates, string_similarity
from tests.conftest import PAPERLESS_URL

TEI_URL = "http://tei.test/v1"


def _tag(id: int, name: str, count: int = 0) -> dict:
    return {
        "id": id,
        "name": name,
        "document_count": count,
        "match": "",
        "matching_algorithm": 0,
        "is_inbox_tag": False,
    }


def _mock_tags(*tags: dict) -> None:
    respx.get(f"{PAPERLESS_URL}/api/tags/").mock(
        return_value=Response(200, json={"count": len(tags), "next": None, "results": list(tags)})
    )


@pytest.fixture
def embeddings_enabled(monkeypatch):
    monkeypatch.setenv("PLLM_LLM__EMBEDDINGS__BASE_URL", TEI_URL)
    monkeypatch.setenv("PLLM_LLM__EMBEDDINGS__MODEL", "test-embed")
    reset_settings_cache()
    yield
    reset_settings_cache()


def _mock_tei(vectors: dict[str, list[float]]) -> respx.Route:
    def responder(request):
        body = json.loads(request.content)
        inputs = body["input"]
        data = [
            {"index": i, "embedding": vectors[text]} for i, text in enumerate(inputs)
        ]
        return Response(200, json={"data": data})

    return respx.post(f"{TEI_URL}/embeddings").mock(side_effect=responder)


@respx.mock
async def test_embed_admission_bounded_by_max_concurrent(
    monkeypatch, embeddings_enabled
):
    """llm.embeddings.max_concurrent caps in-flight requests to the
    embeddings endpoint across parallel callers (entity index rebuilds
    gathering many embed calls)."""
    from app.llm import factory
    from app.services.entity_index import embed_texts

    monkeypatch.setenv("PLLM_LLM__EMBEDDINGS__MAX_CONCURRENT", "2")
    reset_settings_cache()
    monkeypatch.setattr(factory, "_semaphores", {})

    in_flight = peak = 0

    async def responder(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)  # let the gathered calls overlap
        in_flight -= 1
        inputs = json.loads(request.content)["input"]
        return Response(
            200,
            json={"data": [{"index": i, "embedding": [0.0]} for i in range(len(inputs))]},
        )

    respx.post(f"{TEI_URL}/embeddings").mock(side_effect=responder)
    vectors = await asyncio.gather(*(embed_texts([f"text {i}"]) for i in range(8)))
    assert len(vectors) == 8
    assert peak == 2  # saturated the cap, never exceeded it


def test_string_similarity_catches_legal_form_suffixes():
    assert string_similarity("Kraxi", "Kraxi GmbH") > 0.82
    assert string_similarity("Kraxi GmbH", "Finanzamt") < 0.5


@respx.mock
async def test_find_similar_string_only(db, paperless_client):
    """Without embeddings config: pure string ranking, semantic None."""
    _mock_tags(_tag(1, "Kraxi", 3), _tag(2, "Kraxi GmbH", 1), _tag(3, "Steuern", 9))
    results = await find_similar(db, paperless_client, "tag", "Kraxi GmbH & Co", top_k=2)
    # Both Kraxi variants rank on top (substring => perfect partial
    # ratio for either); the unrelated tag is out.
    assert {r["name"] for r in results} == {"Kraxi", "Kraxi GmbH"}
    assert all(r["similarity"] > 0.8 for r in results)
    assert all(r["semantic_score"] is None for r in results)


@respx.mock
async def test_merge_candidates_string_pair_and_target_choice(db, paperless_client):
    _mock_tags(_tag(1, "Kraxi", 5), _tag(2, "Kraxi GmbH", 1), _tag(3, "Steuern", 2))
    pairs = await merge_candidates(db, paperless_client, "tag")
    assert len(pairs) == 1
    p = pairs[0]
    # The better-connected entity is the merge target.
    assert p["target"]["name"] == "Kraxi" and p["source"]["name"] == "Kraxi GmbH"
    assert p["string_score"] > 0.82


@respx.mock
async def test_semantic_similarity_via_tei(db, paperless_client, embeddings_enabled):
    """Embeddings catch semantic twins that string distance misses."""
    _mock_tags(_tag(1, "Finanzamt München", 4), _tag(2, "Tax Office Munich", 1),
               _tag(3, "Rechnung", 7))
    _mock_tei(
        {
            "Finanzamt München": [1.0, 0.0, 0.1],
            "Tax Office Munich": [0.98, 0.05, 0.1],
            "Rechnung": [0.0, 1.0, 0.0],
        }
    )
    pairs = await merge_candidates(db, paperless_client, "tag")
    assert len(pairs) == 1
    assert {pairs[0]["source"]["name"], pairs[0]["target"]["name"]} == {
        "Finanzamt München", "Tax Office Munich"
    }
    assert pairs[0]["semantic_score"] > 0.9
    assert pairs[0]["string_score"] < 0.82  # string alone would have missed it


@respx.mock
async def test_vector_cache_reembeds_only_stale(db, paperless_client, embeddings_enabled):
    _mock_tags(_tag(1, "Alpha", 1), _tag(2, "Beta", 1))
    tei = _mock_tei(
        {
            "Alpha": [1.0, 0.0],
            "Beta": [0.0, 1.0],
            "Beta Renamed": [0.1, 1.0],
            "query": [0.5, 0.5],
        }
    )
    await find_similar(db, paperless_client, "tag", "query")
    first_calls = len(tei.calls)
    assert first_calls == 2  # one batch for entities + one for the query

    # Same taxonomy again: only the query is embedded.
    await find_similar(db, paperless_client, "tag", "query")
    assert len(tei.calls) == first_calls + 1
    embedded = json.loads(tei.calls.last.request.content)["input"]
    assert embedded == ["query"]

    # Rename one entity, drop the other: prune + re-embed the renamed one.
    _mock_tags(_tag(2, "Beta Renamed", 1))
    await find_similar(db, paperless_client, "tag", "query")
    from sqlalchemy import select

    from app.db.models import EntityEmbedding

    rows = (await db.scalars(select(EntityEmbedding))).all()
    assert [(r.entity_id, r.name) for r in rows] == [(2, "Beta Renamed")]
