"""Cohere-compatible rerank client (``[llm.reranker]``).

A second retrieval stage for tools that FIND documents: paperless's
full-text search recalls candidates cheaply, the reranker re-orders
them by real relevance to the query. Everything stays local — the
endpoint is expected on the same network as the LLM (TEI/Infinity
serve `/rerank`; both speak the Cohere shape).

Optional by construction: when no reranker is configured the caller
keeps the first-stage order. Serving quirks stay config, not code.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    # TEI and Infinity serve the Cohere shape at /rerank on a bare
    # host; a ".../v1" base (OpenAI-style config symmetry, or an nginx
    # alias) gets /v1/rerank. Measured against Infinity 0.0.77, which
    # 404s /v1/rerank.
    return f"{base}/rerank"


def rerank_enabled() -> bool:
    return get_settings().llm.reranker.enabled


async def rerank(query: str, texts: list[str], top_n: int | None = None) -> list[int]:
    """Order ``texts`` by relevance to ``query``; returns indices, best
    first. Raises on transport errors — callers decide whether ranked
    order is worth failing over (tools fall back to first-stage order).
    """
    prof = get_settings().llm.reranker
    payload: dict[str, object] = {
        "model": prof.model,
        "query": query,
        "documents": texts,
    }
    if top_n is not None:
        payload["top_n"] = top_n
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            _endpoint(prof.base_url),
            json=payload,
            headers={"Authorization": f"Bearer {prof.api_key}"},
        )
        resp.raise_for_status()
    results = resp.json().get("results", [])
    ranked = sorted(results, key=lambda r: r.get("relevance_score", 0), reverse=True)
    return [int(r["index"]) for r in ranked]
