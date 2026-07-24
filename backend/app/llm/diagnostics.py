"""LLM endpoint diagnostics for the Settings UI: connectivity tests
(one plain completion; for the vision profile with an image attached)
and best-effort capability detection — the server's context window via
well-known metadata endpoints, and the vision endpoint's images-per-
request limit via an empirical probe with tiny images.

Detection is honest about uncertainty: values come back with their
source, and only *exact* findings turn into config suggestions.
"""

from __future__ import annotations

import io
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.settings import ModelSettings

from app.config import get_settings
from app.llm.factory import (
    _build_model,
    _settings_from,
    llm_semaphore,
    resolved_ocr_profile,
)

ProfileName = Literal["agent", "ocr"]
# Connectivity tests cover every configured endpoint family; capability
# detection only makes sense for the two completion profiles.
TestProfileName = Literal["agent", "ocr", "embeddings", "reranker"]

# Diagnostics must fail fast — never the profile's (possibly 600s)
# production timeout.
_TEST_TIMEOUT = 30.0
_HTTP_TIMEOUT = 10.0
# Probe ceiling: nobody batches more pages than this per request.
_IMAGE_PROBE_CEILING = 16
# Predicting how many pages fit the context: each transcribed page
# produces output that shares the window with the input images…
_OUTPUT_RESERVE_PER_PAGE = 1024
# …and the system prompt + user instructions need headroom too.
_CONTEXT_MARGIN = 512
# Output cap for diagnostic calls. A CEILING, not a target: thinking
# models burn hundreds of tokens on reasoning BEFORE the first output
# token, and a tight cap (the original 16) made pydantic-ai fail with
# "token limit exceeded before any response". Runaway generation is
# bounded by the wall-clock timeout, not by this.
_TEST_MAX_TOKENS = 4096


def _diag_settings(profile: ProfileName) -> ModelSettings:
    """Model settings for diagnostic calls: the PROFILE'S production
    sampling + thinking behavior (a test should exercise the same code
    path real runs use — notably thinking on/off), but with the fast
    timeout and a generous output ceiling."""
    s = get_settings().llm
    if profile == "ocr":
        # Mirrors factory.ocr_model(): OCR always runs thinking-off.
        base = _settings_from(s.ocr.sampling, "off", _TEST_TIMEOUT)
    else:
        base = _settings_from(s.agent.sampling, s.agent.thinking, _TEST_TIMEOUT)
    return ModelSettings(**{**base, "max_tokens": _TEST_MAX_TOKENS})  # type: ignore[typeddict-item]


@dataclass
class LlmTestResult:
    ok: bool
    base_url: str
    model: str
    latency_ms: int | None = None
    reply: str | None = None
    error: str | None = None


@dataclass
class LlmDetectResult:
    base_url: str
    model: str
    context_length: int | None = None
    context_source: str | None = None
    max_images: int | None = None
    max_images_exact: bool | None = None
    # Measured cost of ONE page image at the configured render DPI
    # (real request, usage-reported prompt tokens) — and how many such
    # pages fit the detected context window alongside their output.
    render_dpi: int | None = None
    tokens_per_image: int | None = None
    images_in_context: int | None = None
    error: str | None = None
    # dotted config key -> value, ready for the Settings form.
    suggestions: dict[str, int] = field(default_factory=dict)


def _profile_endpoint(profile: ProfileName) -> tuple[str, str, str]:
    """(base_url, model, api_key) with the OCR->agent fallback applied."""
    if profile == "ocr":
        base_url, model, api_key, _ = resolved_ocr_profile()
        return base_url, model, api_key
    p = get_settings().llm.agent
    return p.base_url, p.model, p.api_key


def _probe_png() -> bytes:
    """A small solid-red PNG. 64x64: comfortably above the minimum
    image sizes vision preprocessors require (e.g. Qwen-VL patching)."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _page_png(dpi: int) -> bytes:
    """A blank A4 page rendered at ``dpi``. Vision token cost depends on
    PIXEL DIMENSIONS, not content — a white page measures exactly what a
    real page at the configured render DPI costs the server."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (round(8.27 * dpi), round(11.69 * dpi)), "white").save(
        buf, format="PNG"
    )
    return buf.getvalue()


def images_that_fit(
    context_length: int,
    prompt_overhead: int,
    tokens_per_image: int,
    *,
    output_reserve: int = _OUTPUT_RESERVE_PER_PAGE,
    margin: int = _CONTEXT_MARGIN,
) -> int:
    """How many page images fit one request: every page costs its input
    tokens PLUS reserved output (the transcription shares the window)."""
    usable = context_length - prompt_overhead - margin
    return max(0, usable // (tokens_per_image + output_reserve))


def _err(e: Exception) -> str:
    msg = str(e) or type(e).__name__
    return msg if len(msg) <= 400 else msg[:400] + "…"


async def run_llm_test(profile: TestProfileName) -> LlmTestResult:
    """One real call against the profile's endpoint. The vision profile
    gets an image attached — a text-only success would say nothing about
    multimodal serving; embeddings/reranker use their production client
    code paths."""
    if profile == "embeddings":
        return await _test_embeddings()
    if profile == "reranker":
        return await _test_reranker()
    base_url, model_name, api_key = _profile_endpoint(profile)
    result = LlmTestResult(ok=False, base_url=base_url, model=model_name)
    if not base_url or not model_name:
        result.error = "endpoint or model not configured"
        return result
    model = _build_model(base_url, model_name, api_key, _TEST_TIMEOUT)
    agent: Agent[None, str] = Agent(model, model_settings=_diag_settings(profile))
    if profile == "ocr":
        parts: list[str | BinaryContent] = [
            "In one word: what color is the attached image?",
            BinaryContent(data=_probe_png(), media_type="image/png"),
        ]
    else:
        parts = ["Reply with the single word: pong"]
    sem = llm_semaphore(base_url, max(1, get_settings().llm.agent.max_concurrent))
    started = time.monotonic()
    try:
        async with sem:
            reply = (await agent.run(parts)).output
    except Exception as e:  # noqa: BLE001 — every failure mode is a result here
        result.error = _err(e)
        return result
    result.ok = True
    result.latency_ms = int((time.monotonic() - started) * 1000)
    result.reply = reply.strip()[:200]
    return result


async def _test_embeddings() -> LlmTestResult:
    prof = get_settings().llm.embeddings
    result = LlmTestResult(ok=False, base_url=prof.base_url, model=prof.model)
    if not prof.enabled:
        result.error = "endpoint or model not configured"
        return result
    from app.services.entity_index import embed_texts

    started = time.monotonic()
    try:
        [vector] = await embed_texts(["connectivity test"])
    except Exception as e:  # noqa: BLE001
        result.error = _err(e)
        return result
    result.ok = True
    result.latency_ms = int((time.monotonic() - started) * 1000)
    result.reply = f"{len(vector)}-dim vector"
    return result


async def _test_reranker() -> LlmTestResult:
    prof = get_settings().llm.reranker
    result = LlmTestResult(ok=False, base_url=prof.base_url, model=prof.model)
    if not prof.enabled:
        result.error = "endpoint or model not configured"
        return result
    from app.llm.rerank import rerank

    started = time.monotonic()
    try:
        # A pair with an obvious answer — the reply shows whether the
        # model actually ranked, not just responded.
        order = await rerank(
            "Which document is an invoice?",
            ["The cat sat on the mat.", "Invoice no. 42, total EUR 99.50."],
        )
    except Exception as e:  # noqa: BLE001
        result.error = _err(e)
        return result
    result.ok = True
    result.latency_ms = int((time.monotonic() - started) * 1000)
    result.reply = (
        "ranked the invoice first — sane"
        if order and order[0] == 1
        else f"responded, but ranked document {order[0] + 1} first"
        if order
        else "responded with no results"
    )
    return result


# ----- context-window detection ----------------------------------------

# (field name in the /models entry, human-readable source label)
_CONTEXT_KEYS = (
    ("max_model_len", "max_model_len (vLLM)"),
    ("context_length", "context_length"),
    ("max_context_length", "max_context_length"),
    ("context_window", "context_window"),
)


async def detect_context_length(
    base_url: str, model: str, api_key: str
) -> tuple[int | None, str | None]:
    """The server's context window, from whichever metadata endpoint
    this server family exposes: OpenAI-compatible /models (vLLM et al.),
    llama.cpp /props, Ollama /api/show. None when nothing answered."""
    base = base_url.rstrip("/")
    origin = base.removesuffix("/v1")
    headers = {"Authorization": f"Bearer {api_key or 'unused'}"}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers) as client:
        # 1) OpenAI-compatible model listing.
        try:
            r = await client.get(f"{base}/models")
            if r.status_code == 200:
                data = r.json().get("data") or []
                entry = next(
                    (d for d in data if d.get("id") == model), None
                ) or (data[0] if len(data) == 1 else None)
                if entry:
                    for key, label in _CONTEXT_KEYS:
                        v = entry.get(key)
                        if isinstance(v, int) and v > 0:
                            return v, f"{label} via /models"
                    v = (entry.get("meta") or {}).get("n_ctx_train")
                    if isinstance(v, int) and v > 0:
                        return v, "n_ctx_train via /models (llama.cpp; training context)"
        except Exception:  # noqa: BLE001 — fall through to the next family
            pass
        # 2) llama.cpp server properties (the SERVING context, which is
        # what actually matters — n_ctx_train above is the fallback).
        try:
            r = await client.get(f"{origin}/props")
            if r.status_code == 200:
                v = (r.json().get("default_generation_settings") or {}).get("n_ctx")
                if isinstance(v, int) and v > 0:
                    return v, "n_ctx via /props (llama.cpp)"
        except Exception:  # noqa: BLE001
            pass
        # 3) Ollama. num_ctx (the runtime window it actually serves)
        # beats the model card's maximum.
        try:
            r = await client.post(f"{origin}/api/show", json={"model": model})
            if r.status_code == 200:
                j = r.json()
                m = re.search(r"num_ctx\s+(\d+)", str(j.get("parameters") or ""))
                if m:
                    return int(m.group(1)), "num_ctx via Ollama /api/show"
                for k, v in (j.get("model_info") or {}).items():
                    if k.endswith(".context_length") and isinstance(v, int) and v > 0:
                        return v, f"{k} via Ollama /api/show (model maximum)"
        except Exception:  # noqa: BLE001
            pass
    return None, None


def suggest_input_clamp(context_length: int) -> int:
    """`max_input_tokens` clamps tool results and is documented to stay
    FAR below the server window (prompt + transcript + output all share
    it). Suggest ~3/4 of the window, floored to 1k steps."""
    return max(4096, (context_length * 3 // 4) // 1024 * 1024)


# ----- images-per-request probe ----------------------------------------


async def probe_image_limit(
    attempt: Callable[[int], Awaitable[None]], ceiling: int = _IMAGE_PROBE_CEILING
) -> tuple[int, bool, str | None]:
    """Find the server's images-per-request limit empirically.

    ``attempt(k)`` performs one real request with ``k`` images and
    raises on rejection. Doubling ladder then binary refinement —
    O(log ceiling) requests. Returns (max_ok, exact, error):
    exact=False means the ceiling was reached without a failure (no
    server-side cap found up to ``ceiling``); max_ok=0 means even a
    single image failed (error carries why).
    """
    error: str | None = None
    try:
        await attempt(1)
    except Exception as e:  # noqa: BLE001 — vision itself is broken
        return 0, True, _err(e)
    ok, fail = 1, None
    k = 2
    while k <= ceiling:
        try:
            await attempt(k)
            ok = k
        except Exception as e:  # noqa: BLE001 — treat as the limit
            fail, error = k, _err(e)
            break
        k *= 2
    if fail is None:
        return ok, False, None
    while fail - ok > 1:
        mid = (ok + fail) // 2
        try:
            await attempt(mid)
            ok = mid
        except Exception:  # noqa: BLE001
            fail = mid
    return ok, True, error


async def run_llm_detect(profile: ProfileName) -> LlmDetectResult:
    base_url, model_name, api_key = _profile_endpoint(profile)
    result = LlmDetectResult(base_url=base_url, model=model_name)
    if not base_url or not model_name:
        result.error = "endpoint or model not configured"
        return result

    result.context_length, result.context_source = await detect_context_length(
        base_url, model_name, api_key
    )
    if profile == "agent":
        if result.context_length:
            result.suggestions["llm.agent.max_input_tokens"] = suggest_input_clamp(
                result.context_length
            )
        else:
            result.error = (
                "server exposes no context metadata (/models, /props, /api/show)"
            )
        return result

    # Vision profile: probe the images-per-request limit with tiny
    # images and minimal output — a handful of near-free requests.
    model = _build_model(base_url, model_name, api_key, _TEST_TIMEOUT)
    # Same generous output ceiling as the connectivity test: with a
    # tight cap, a thinking VLM would hit the token limit and the probe
    # would MISREAD it as the server's image limit.
    agent: Agent[None, str] = Agent(model, model_settings=_diag_settings(profile))
    png = _probe_png()
    sem = llm_semaphore(base_url, max(1, get_settings().llm.agent.max_concurrent))

    async def attempt(k: int) -> None:
        parts: list[str | BinaryContent] = ["Reply with the single word: ok"]
        parts += [BinaryContent(data=png, media_type="image/png")] * k
        async with sem:
            await agent.run(parts)

    max_images, exact, error = await probe_image_limit(attempt)
    result.max_images = max_images or None
    result.max_images_exact = exact if max_images else None
    if max_images == 0:
        result.error = error
        return result

    # Measure what one PAGE actually costs: a text-only call gives the
    # prompt overhead, adding a single blank A4 page at the configured
    # render DPI gives the per-image token cost — usage-reported by the
    # server itself, so preprocessor resizing/patching is priced in.
    dpi = get_settings().llm.ocr.render_dpi
    result.render_dpi = dpi
    try:
        async with sem:
            text_run = await agent.run(["Reply with the single word: ok"])
        page = BinaryContent(data=_page_png(dpi), media_type="image/png")
        async with sem:
            page_run = await agent.run(["Reply with the single word: ok", page])
        overhead = text_run.usage().input_tokens or 0
        with_page = page_run.usage().input_tokens or 0
        if with_page > overhead > 0:
            result.tokens_per_image = with_page - overhead
            if result.context_length:
                result.images_in_context = images_that_fit(
                    result.context_length, overhead, result.tokens_per_image
                )
    except Exception:  # noqa: BLE001 — prediction is best-effort; the
        # probe result above stands on its own.
        pass

    # Suggest the BINDING constraint: server cap (when one exists) vs.
    # what the context window actually fits — whichever is smaller.
    candidates = [_IMAGE_PROBE_CEILING]
    if exact:
        candidates.append(max_images)
    if result.images_in_context is not None:
        candidates.append(result.images_in_context)
    if exact or result.images_in_context is not None:
        result.suggestions["llm.ocr.max_images_per_request"] = max(1, min(candidates))
    return result
