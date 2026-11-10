"""Model factory: turns config profiles into pydantic-ai models.

All serving-setup quirks flow from config (DESIGN.md "Model profiles");
nothing here assumes a particular server beyond OpenAI compatibility.
"""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any, Literal

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from app.config import AgentProfile, OcrProfile, SamplingOverrides, get_settings
from app.llm.timing import TimedModel, TimeLimitedModel

# One semaphore per endpoint URL, shared by every consumer in this
# process (agent runs, OCR, interactive chat). Sized via config
# `max_concurrent` to respect server-side limits (e.g. vLLM max-num-seqs
# shared with other services). AUDIT BC-F10: keyed by base_url ONLY —
# keying by size created a SECOND semaphore for the same endpoint when
# max_concurrent changed at runtime (brief over-admission + stale
# entries). A size change replaces the semaphore: old holders drain on
# the old object, a bounded one-time overlap.


class AdmissionTimeout(RuntimeError):
    """No endpoint permit inside ``llm.admission_timeout_seconds``.

    Raised instead of waiting forever, so the step engine can record a
    failed attempt (the alternative — the pre-fix behaviour — was a step
    parked in ``running`` with no timeout covering the acquire)."""

    def __init__(self, seconds: float) -> None:
        super().__init__(
            f"no LLM endpoint permit within {seconds:g}s — another request "
            "holds it, or a tool ran inside this task's own admission window"
        )


class EndpointSemaphore(asyncio.Semaphore):
    """Semaphore that is reentrant for the work already holding a permit.

    ``run_agent_turn`` keeps a permit for the WHOLE ``agent.run``, and the
    tools executed inside that run may need the SAME endpoint: when
    embeddings (or OCR) share the agent's base_url, `embeddings_semaphore()`
    resolves to this very object. `asyncio.Semaphore` is not reentrant, so
    the turn waited on a permit only it could ever release — and nothing
    times out an acquire, so the step sat in ``running`` forever while the
    UI showed "working" (incident: the first taxonomy session, tag "Rente",
    with ``llm.agent.max_concurrent=1`` -> every embedding-backed tool).

    Ownership is tracked in a ContextVar, NOT by task identity: pydantic-ai
    runs each graph step (model request + the tool calls it triggers) in a
    task of its own (`pydantic_graph.graph_builder` -> anyio task group),
    so the tool body is a different task than the one that took the
    permit — only the inherited context tells them apart. Every unrelated
    task still queues, so the cross-task admission the shared endpoint
    needs is unchanged. A child task that outlives the hold keeps the
    reentrant view (a bounded overlap, like the size-change case above).

    ``admission_timeout`` bounds the wait for callers that do NOT already
    hold a permit (a nesting bug or over-subscription then fails the step
    instead of parking it). It never applies to the reentrant path.
    """

    def __init__(self, value: int = 1, *, admission_timeout: float | None = None):
        super().__init__(value)
        self.admission_timeout = admission_timeout

    async def acquire(self) -> Literal[True]:
        if self in _held_permits.get():
            # Re-entrant: this context (including any task created while
            # the permit is held) reuses the permit instead of queueing
            # behind itself. Only the bookkeeping counter moves.
            _held_permits.set((*_held_permits.get(), self))
            return True
        await self._acquire_permit()
        _held_permits.set((*_held_permits.get(), self))
        return True

    async def _acquire_permit(self) -> None:
        # Outside a task (callbacks, sync bootstrap) asyncio.timeout() is
        # unusable — the unbounded acquire is the only option there.
        if self.admission_timeout is None or asyncio.current_task() is None:
            await super().acquire()
            return
        try:
            async with asyncio.timeout(self.admission_timeout):
                await super().acquire()
        except TimeoutError as exc:
            # asyncio.Semaphore unwinds its waiter on cancellation, so the
            # count and the wake-up order stay consistent.
            raise AdmissionTimeout(self.admission_timeout) from exc

    def release(self) -> None:
        held = _held_permits.get()
        if self in held:
            # Unwind one level. Only the LAST level returns the permit.
            idx = held.index(self)
            remaining = held[:idx] + held[idx + 1 :]
            _held_permits.set(remaining)
            if self in remaining:
                return
        # Released from a context that never marked the hold (defensive:
        # a cross-task release, or an acquire that did not complete).
        super().release()


# Which endpoint permits the CURRENT context holds. Propagated into tasks
# created while a permit is held, which is what makes the reentrancy work
# for tool calls (see EndpointSemaphore).
_held_permits: contextvars.ContextVar[tuple[EndpointSemaphore, ...]] = contextvars.ContextVar(
    "llm_endpoint_permits", default=()
)


_semaphores: dict[str, tuple[int, EndpointSemaphore]] = {}


def llm_semaphore(base_url: str, max_concurrent: int) -> EndpointSemaphore:
    entry = _semaphores.get(base_url)
    if entry is None or entry[0] != max_concurrent:
        entry = (max_concurrent, EndpointSemaphore(max_concurrent))
        _semaphores[base_url] = entry
    # Re-read the safety-net budget: it is configurable at runtime, and
    # the semaphore object survives until the size changes.
    entry[1].admission_timeout = get_settings().llm.admission_timeout_seconds
    return entry[1]


def embeddings_semaphore() -> EndpointSemaphore:
    """Endpoint admission for embeddings calls, honoring
    ``llm.embeddings.max_concurrent``. Same shared-endpoint reasoning
    as ocr_model: when embeddings point at the agent's endpoint, a
    distinct cap would make the two consumers alternately REPLACE the
    shared semaphore (over-admitting on every swap) — shared endpoint =
    shared admission, keyed on the agent's URL string."""
    s = get_settings().llm
    emb = s.embeddings
    if emb.base_url.rstrip("/") == s.agent.base_url.rstrip("/"):
        return llm_semaphore(s.agent.base_url, s.agent.max_concurrent)
    return llm_semaphore(emb.base_url, emb.max_concurrent)


def _settings_from(
    sampling: SamplingOverrides,
    thinking: str = "server_default",
    timeout: float | None = None,
) -> ModelSettings:
    settings: dict[str, Any] = {}
    if timeout is not None:
        settings["timeout"] = timeout
    if sampling.temperature is not None:
        settings["temperature"] = sampling.temperature
    if sampling.top_p is not None:
        settings["top_p"] = sampling.top_p
    if sampling.max_tokens is not None:
        settings["max_tokens"] = sampling.max_tokens
    if sampling.presence_penalty is not None:
        settings["presence_penalty"] = sampling.presence_penalty
    if sampling.frequency_penalty is not None:
        settings["frequency_penalty"] = sampling.frequency_penalty
    # Non-OpenAI-standard levers travel via extra_body — vLLM, SGLang,
    # llama.cpp and Ollama's OpenAI compat all accept them there (and
    # tolerate unknown parameters). These are the anti-repetition-loop
    # knobs for pages a VLM can't read.
    extra: dict[str, Any] = {}
    if sampling.repetition_penalty is not None:
        extra["repetition_penalty"] = sampling.repetition_penalty
    if sampling.top_k is not None:
        extra["top_k"] = sampling.top_k
    if sampling.min_p is not None:
        extra["min_p"] = sampling.min_p
    if thinking != "server_default":
        # vLLM/SGLang-style opt-in/out; harmless elsewhere.
        extra["chat_template_kwargs"] = {"enable_thinking": thinking == "on"}
    if extra:
        settings["extra_body"] = extra
    return ModelSettings(**settings)  # type: ignore[typeddict-item]


def _build_model(base_url: str, model: str, api_key: str, timeout: float | None) -> Model:
    # TimedModel stamps per-call metrics (duration, tokens, tps, ttft
    # when streaming) into each response's provider_details.
    # TimeLimitedModel is OUTERMOST: the wall-clock cap spans the whole
    # call including stream consumption — the guard against endpoints
    # that never finish (HTTP read timeouts are per-chunk and can't
    # catch a stream that keeps dribbling tokens).
    return TimeLimitedModel(
        TimedModel(
            OpenAIChatModel(model, provider=OpenAIProvider(base_url=base_url, api_key=api_key))
        ),
        wall_timeout=timeout,
    )


def agent_model(profile: AgentProfile | None = None) -> Model:
    p = profile or get_settings().llm.agent
    return _build_model(p.base_url, p.model, p.api_key, p.timeout_seconds)


def agent_model_settings(profile: AgentProfile | None = None) -> ModelSettings:
    p = profile or get_settings().llm.agent
    return _settings_from(p.sampling, p.thinking, p.timeout_seconds)


def resolved_ocr_profile() -> tuple[str, str, str, OcrProfile]:
    """OCR endpoint/model, falling back to the agent profile when unset.

    Returns (base_url, model, api_key, ocr_profile).
    """
    s = get_settings().llm
    ocr = s.ocr
    return (
        ocr.base_url or s.agent.base_url,
        ocr.model or s.agent.model,
        ocr.api_key or s.agent.api_key,
        ocr,
    )


def ocr_model() -> tuple[Model, ModelSettings, OcrProfile, asyncio.Semaphore]:
    base_url, model, api_key, ocr = resolved_ocr_profile()
    # Wall-clock budget falls back to the agent profile's, like the
    # endpoint itself.
    timeout = ocr.timeout_seconds or get_settings().llm.agent.timeout_seconds
    # AUDIT BC-F10: the OCR endpoint's admission is tunable on its own
    # profile; only fall back to the agent's when unset.
    # Reinspection: honor ocr.max_concurrent ONLY when OCR has its own
    # base_url. When OCR falls back to the agent's endpoint, a distinct
    # size would make agent turns and OCR runs alternately REPLACE the
    # shared semaphore — each replacement over-admits while holders of
    # the previous object are still inside. Shared endpoint = shared
    # admission.
    agent_cap = get_settings().llm.agent.max_concurrent
    cap = (ocr.max_concurrent or agent_cap) if ocr.base_url else agent_cap
    sem = llm_semaphore(base_url, cap)
    # OCR is a plain completion; thinking adds latency for no benefit.
    return (
        _build_model(base_url, model, api_key, timeout),
        _settings_from(ocr.sampling, "off", timeout),
        ocr,
        sem,
    )
