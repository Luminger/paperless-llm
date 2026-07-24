"""Settings LLM diagnostics: context-window detection across server
families and the empirical images-per-request probe."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.llm.diagnostics import (
    _TEST_TIMEOUT,
    _diag_settings,
    detect_context_length,
    images_that_fit,
    probe_image_limit,
    run_llm_test,
    suggest_input_clamp,
)

BASE = "http://llm.test/v1"


@respx.mock
async def test_detect_vllm_max_model_len():
    respx.get(f"{BASE}/models").mock(
        return_value=Response(
            200,
            json={"data": [{"id": "qwen3", "max_model_len": 32768}]},
        )
    )
    ctx, source = await detect_context_length(BASE, "qwen3", "k")
    assert ctx == 32768
    assert "vLLM" in source


@respx.mock
async def test_detect_matches_model_id_among_many():
    respx.get(f"{BASE}/models").mock(
        return_value=Response(
            200,
            json={
                "data": [
                    {"id": "other", "max_model_len": 1024},
                    {"id": "qwen3", "max_model_len": 65536},
                ]
            },
        )
    )
    ctx, _ = await detect_context_length(BASE, "qwen3", "k")
    assert ctx == 65536


@respx.mock
async def test_detect_llamacpp_props_serving_context():
    """/models without context info -> /props n_ctx (the SERVING window)."""
    respx.get(f"{BASE}/models").mock(
        return_value=Response(200, json={"data": [{"id": "qwen3"}]})
    )
    respx.get("http://llm.test/props").mock(
        return_value=Response(
            200, json={"default_generation_settings": {"n_ctx": 16384}}
        )
    )
    ctx, source = await detect_context_length(BASE, "qwen3", "k")
    assert ctx == 16384
    assert "llama.cpp" in source


@respx.mock
async def test_detect_ollama_num_ctx_beats_model_maximum():
    respx.get(f"{BASE}/models").mock(return_value=Response(404))
    respx.get("http://llm.test/props").mock(return_value=Response(404))
    respx.post("http://llm.test/api/show").mock(
        return_value=Response(
            200,
            json={
                "parameters": "num_ctx  8192\nstop  <|im_end|>",
                "model_info": {"qwen2.context_length": 131072},
            },
        )
    )
    ctx, source = await detect_context_length(BASE, "qwen3", "k")
    assert ctx == 8192  # what it actually serves, not the card maximum
    assert "num_ctx" in source


@respx.mock
async def test_detect_nothing_found():
    respx.get(f"{BASE}/models").mock(return_value=Response(500))
    respx.get("http://llm.test/props").mock(return_value=Response(404))
    respx.post("http://llm.test/api/show").mock(return_value=Response(404))
    ctx, source = await detect_context_length(BASE, "qwen3", "k")
    assert ctx is None and source is None


def test_diag_settings_leave_room_for_thinking(monkeypatch):
    """Regression: max_tokens=16 made thinking models fail with 'token
    limit exceeded before any response'. The cap must comfortably hold
    a reasoning trace; runaways are bounded by the wall timeout."""
    for profile in ("agent", "ocr"):
        s = _diag_settings(profile)
        assert s["max_tokens"] >= 1024
        assert s["timeout"] == _TEST_TIMEOUT


def test_diag_settings_mirror_production_thinking(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings().llm.agent, "thinking", "on")
    agent = _diag_settings("agent")
    assert agent["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
    # OCR always runs thinking-off, exactly like factory.ocr_model().
    ocr = _diag_settings("ocr")
    assert ocr["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_suggest_input_clamp_leaves_headroom():
    assert suggest_input_clamp(32768) == 24576  # 3/4, 1k-aligned
    assert suggest_input_clamp(1000) == 4096  # floor


def test_images_that_fit_reserves_output_per_page():
    # 32k window, 60-token prompt, ~1.9k tokens per A4 page at 150 DPI:
    # each page costs input + reserved output, so ~11 pages, not ~17.
    n = images_that_fit(32768, 60, 1900)
    assert n == (32768 - 60 - 512) // (1900 + 1024)
    assert n == 11


def test_images_that_fit_never_negative():
    assert images_that_fit(2048, 4000, 1900) == 0


def _attempts(limit: int, log: list[int]):
    async def attempt(k: int) -> None:
        log.append(k)
        if k > limit:
            raise RuntimeError(f"too many images: {k}")

    return attempt


async def test_probe_finds_exact_limit():
    log: list[int] = []
    max_ok, exact, error = await probe_image_limit(_attempts(3, log))
    assert (max_ok, exact) == (3, True)
    assert "too many images" in error
    assert len(log) <= 7  # O(log ceiling), not linear


async def test_probe_power_of_two_limit():
    max_ok, exact, _ = await probe_image_limit(_attempts(8, []))
    assert (max_ok, exact) == (8, True)


async def test_probe_no_cap_up_to_ceiling():
    max_ok, exact, error = await probe_image_limit(_attempts(999, []))
    assert (max_ok, exact, error) == (16, False, None)


async def test_probe_single_image_failure_is_an_error():
    max_ok, exact, error = await probe_image_limit(_attempts(0, []))
    assert max_ok == 0 and exact
    assert error


async def test_probe_limit_one():
    max_ok, exact, _ = await probe_image_limit(_attempts(1, []))
    assert (max_ok, exact) == (1, True)


@pytest.mark.parametrize("limit", list(range(1, 17)))
async def test_probe_correct_for_every_limit(limit):
    max_ok, exact, _ = await probe_image_limit(_attempts(limit, []))
    assert max_ok == limit
    assert exact is (limit < 16)


# ----- embeddings / reranker connectivity ------------------------------


async def test_embeddings_unconfigured_is_a_clean_error():
    result = await run_llm_test("embeddings")
    assert not result.ok
    assert "not configured" in result.error


async def test_reranker_unconfigured_is_a_clean_error():
    result = await run_llm_test("reranker")
    assert not result.ok
    assert "not configured" in result.error


@respx.mock
async def test_embeddings_test_reports_dimension(monkeypatch):
    from app.config import get_settings

    emb = get_settings().llm.embeddings
    monkeypatch.setattr(emb, "base_url", "http://emb.test/v1")
    monkeypatch.setattr(emb, "model", "bge-m3")
    respx.post("http://emb.test/v1/embeddings").mock(
        return_value=Response(
            200, json={"data": [{"index": 0, "embedding": [0.1] * 1024}]}
        )
    )
    result = await run_llm_test("embeddings")
    assert result.ok
    assert result.reply == "1024-dim vector"
    assert result.model == "bge-m3"


@respx.mock
async def test_reranker_test_sanity_checks_the_ranking(monkeypatch):
    from app.config import get_settings

    rer = get_settings().llm.reranker
    monkeypatch.setattr(rer, "base_url", "http://rer.test")
    monkeypatch.setattr(rer, "model", "bge-reranker")
    respx.post("http://rer.test/rerank").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.98},
                    {"index": 0, "relevance_score": 0.02},
                ]
            },
        )
    )
    result = await run_llm_test("reranker")
    assert result.ok
    assert "sane" in result.reply


@respx.mock
async def test_reranker_test_flags_nonsense_ranking(monkeypatch):
    from app.config import get_settings

    rer = get_settings().llm.reranker
    monkeypatch.setattr(rer, "base_url", "http://rer.test")
    monkeypatch.setattr(rer, "model", "bge-reranker")
    respx.post("http://rer.test/rerank").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.1},
                ]
            },
        )
    )
    result = await run_llm_test("reranker")
    assert result.ok  # reachable — but the reply carries the warning
    assert "ranked document 1 first" in result.reply
