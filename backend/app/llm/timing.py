"""Per-call LLM middleware: metrics + wall-clock time limit.

``TimedModel`` wraps any pydantic-ai model and stamps a ``pllm_timing``
dict into each response's ``provider_details``, which is serialized into
the session's message history — so timing travels with the canonical
record and surfaces in the transcript:

- started_at / finished_at (UTC ISO)
- duration_s: wall time of the call
- ttft_s: time to first chunk — only measurable on streaming calls,
  ``None`` for non-streaming (the body arrives as one blob)
- input_tokens / output_tokens
- tps: output tokens per second of generation time (duration minus
  TTFT where known)

``TimeLimitedModel`` enforces a max WALL-CLOCK execution time on every
call. HTTP-level timeouts are not enough: a read timeout is per-chunk,
so a streaming endpoint that keeps dribbling tokens (or a model stuck
in a generation loop) can run forever without ever tripping it. The
cap spans the ENTIRE call — connect, first token, and the full stream
consumption — and turns overruns into a legible ``LlmTimeoutError``
that the step machinery records and auto-retries like any failure.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

TIMING_KEY = "pllm_timing"


class LlmTimeoutError(Exception):
    """An LLM call exceeded its configured wall-clock budget."""

    def __init__(self, seconds: float) -> None:
        super().__init__(
            f"LLM call exceeded the configured max execution time "
            f"({seconds:g}s). The model endpoint may be overloaded or "
            f"stuck; if long calls are legitimate here, raise the "
            f"profile's timeout_seconds under Settings → Models."
        )
        self.seconds = seconds


def _timing(
    started: datetime, t0: float, response: ModelResponse, ttft: float | None
) -> dict[str, Any]:
    duration = perf_counter() - t0
    out_tokens = response.usage.output_tokens or 0
    gen_time = duration - (ttft or 0.0)
    return {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "duration_s": round(duration, 3),
        "ttft_s": round(ttft, 3) if ttft is not None else None,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": out_tokens,
        "tps": round(out_tokens / gen_time, 1) if out_tokens and gen_time > 0 else None,
    }


def _attach(response: ModelResponse, timing: dict[str, Any]) -> ModelResponse:
    response.provider_details = {**(response.provider_details or {}), TIMING_KEY: timing}
    return response


class TimeLimitedModel(WrapperModel):
    """Hard wall-clock cap around every model call (see module doc)."""

    def __init__(self, wrapped, wall_timeout: float | None) -> None:
        super().__init__(wrapped)
        self.wall_timeout = wall_timeout

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        if self.wall_timeout is None:
            return await super().request(messages, model_settings, model_request_parameters)
        try:
            async with asyncio.timeout(self.wall_timeout):
                return await super().request(
                    messages, model_settings, model_request_parameters
                )
        except TimeoutError as e:
            raise LlmTimeoutError(self.wall_timeout) from e

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        if self.wall_timeout is None:
            async with self.wrapped.request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as stream:
                yield stream
            return
        # The timeout context spans the caller's ENTIRE consumption of
        # the stream (the yield happens inside it), so a stream that
        # never finishes is cancelled when the budget runs out.
        try:
            async with asyncio.timeout(self.wall_timeout):
                async with self.wrapped.request_stream(
                    messages, model_settings, model_request_parameters, run_context
                ) as stream:
                    yield stream
        except TimeoutError as e:
            raise LlmTimeoutError(self.wall_timeout) from e


class TimedModel(WrapperModel):
    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        started, t0 = datetime.now(UTC), perf_counter()
        response = await super().request(messages, model_settings, model_request_parameters)
        return _attach(response, _timing(started, t0, response, ttft=None))

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        started, t0 = datetime.now(UTC), perf_counter()
        async with self.wrapped.request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as stream:
            original_get = stream.get

            def get_with_timing() -> ModelResponse:
                response = original_get()
                # The stream stamps perf_counter() on its first surfaced
                # chunk; relative to our request start that's the TTFT.
                first = getattr(stream, "_first_chunk_monotonic", None)
                ttft = (first - t0) if first is not None else None
                return _attach(response, _timing(started, t0, response, ttft))

            stream.get = get_with_timing  # type: ignore[method-assign]
            yield stream
