"""Rerank second stage: Cohere-compatible client + the find_documents
tool's two-stage retrieval (full-text recall, semantic re-order), with
graceful fallback when no reranker is configured or it fails."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import respx
from httpx import Response

from app.config import reset_settings_cache
from app.llm.rerank import _endpoint, rerank
from tests.conftest import PAPERLESS_URL

RERANK_URL = "http://rerank.test"


@pytest.fixture
def reranker_enabled(monkeypatch):
    monkeypatch.setenv("PLLM_LLM__RERANKER__BASE_URL", RERANK_URL)
    monkeypatch.setenv("PLLM_LLM__RERANKER__MODEL", "test-rerank")
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_endpoint_normalization():
    assert _endpoint("http://x:8091") == "http://x:8091/rerank"
    assert _endpoint("http://x:8091/v1") == "http://x:8091/v1/rerank"
    assert _endpoint("http://x:8091/v1/") == "http://x:8091/v1/rerank"


@respx.mock
async def test_rerank_orders_by_score(reranker_enabled):
    route = respx.post(f"{RERANK_URL}/rerank").mock(
        return_value=Response(200, json={"results": [
            {"index": 0, "relevance_score": 0.1},
            {"index": 2, "relevance_score": 0.9},
            {"index": 1, "relevance_score": 0.5},
        ]})
    )
    assert await rerank("q", ["a", "b", "c"]) == [2, 1, 0]
    body = route.calls.last.request.content
    assert b'"model": "test-rerank"' in body or b'"model":"test-rerank"' in body


def _doc(i: int, title: str, content: str) -> dict:
    return {
        "id": i, "title": title, "content": content, "tags": [],
        "correspondent": None, "document_type": None, "storage_path": None,
        "created": "2024-01-01", "custom_fields": [],
    }


def _mock_search():
    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(
        return_value=Response(200, json={
            "count": 3, "next": None, "previous": None,
            "results": [
                _doc(1, "Grocery list", "milk, eggs"),
                _doc(2, "Insurance policy", "household insurance terms"),
                _doc(3, "Insurance invoice", "premium payment 2024"),
            ],
        })
    )


@respx.mock
async def test_find_documents_without_reranker(paperless_client):
    """No reranker configured: full-text order is kept, marked as such."""
    from app.agents.tools import find_documents

    _mock_search()
    ctx = SimpleNamespace(deps=SimpleNamespace(paperless=paperless_client))
    out = await find_documents(ctx, "insurance", top_k=2)
    assert out["reranked"] is False
    assert [d["id"] for d in out["documents"]] == [1, 2]
    assert out["total_matches"] == 3
    assert out["documents"][0]["snippet"] == "milk, eggs"


@respx.mock
async def test_find_documents_reranked(paperless_client, reranker_enabled):
    from app.agents.tools import find_documents

    _mock_search()
    respx.post(f"{RERANK_URL}/rerank").mock(
        return_value=Response(200, json={"results": [
            {"index": 2, "relevance_score": 0.95},
            {"index": 1, "relevance_score": 0.80},
            {"index": 0, "relevance_score": 0.05},
        ]})
    )
    ctx = SimpleNamespace(deps=SimpleNamespace(paperless=paperless_client))
    out = await find_documents(ctx, "insurance", top_k=2)
    assert out["reranked"] is True
    assert [d["id"] for d in out["documents"]] == [3, 2]


@respx.mock
async def test_find_documents_survives_rerank_outage(paperless_client, reranker_enabled):
    from app.agents.tools import find_documents

    _mock_search()
    respx.post(f"{RERANK_URL}/rerank").mock(return_value=Response(503))
    ctx = SimpleNamespace(deps=SimpleNamespace(paperless=paperless_client))
    out = await find_documents(ctx, "insurance", top_k=2)
    assert out["reranked"] is False
    assert [d["id"] for d in out["documents"]] == [1, 2]
