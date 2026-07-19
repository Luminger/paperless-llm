"""TimedModel: per-call metrics stamped into provider_details, surfaced
in the transcript."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from app.llm.timing import LlmTimeoutError, TimedModel, TimeLimitedModel
from app.services.transcript import derive_transcript


async def test_non_streaming_request_gets_timing():
    agent = Agent(TimedModel(TestModel(custom_output_text="hello world")))
    result = await agent.run("hi")
    response = result.all_messages()[-1]
    timing = response.provider_details["pllm_timing"]
    assert timing["duration_s"] >= 0
    assert timing["ttft_s"] is None  # not measurable without streaming
    assert timing["output_tokens"] > 0
    assert "started_at" in timing and "finished_at" in timing


async def test_streaming_request_measures_ttft():
    agent = Agent(TimedModel(TestModel(custom_output_text="hello world")))

    async def handler(ctx, events):
        async for _ in events:
            pass

    result = await agent.run("hi", event_stream_handler=handler)
    response = result.all_messages()[-1]
    timing = response.provider_details["pllm_timing"]
    assert timing["ttft_s"] is not None
    assert 0 <= timing["ttft_s"] <= timing["duration_s"] + 0.001


def test_transcript_attaches_timing_to_every_item_of_its_response():
    timing = {"duration_s": 2.5, "tps": 40.0, "ttft_s": 0.3}
    history = [
        {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "go"}]},
        {
            "kind": "response",
            "provider_details": {"pllm_timing": timing},
            "parts": [
                {"part_kind": "tool-call", "tool_name": "a", "args": {}, "tool_call_id": "1"},
                {"part_kind": "tool-call", "tool_name": "b", "args": {}, "tool_call_id": "2"},
            ],
        },
        {
            "kind": "response",
            "provider_details": {"pllm_timing": timing | {"duration_s": 1.0}},
            "parts": [{"part_kind": "text", "content": "done"}],
        },
    ]
    t = derive_transcript(history)
    tools = [i for i in t if i.role == "tool"]
    # Both tool widgets came from the same LLM call — both carry it.
    assert tools[0].timing.model_dump(exclude_none=True) == timing
    assert tools[1].timing.model_dump(exclude_none=True) == timing
    agent_item = next(i for i in t if i.role == "agent")
    assert agent_item.timing.duration_s == 1.0


def test_pydantic_ai_private_field_still_exists():
    """AUDIT BC-F19: timing reads pydantic-ai's private
    `_first_chunk_monotonic`. It degrades silently if upstream renames
    it — this test turns a dependency bump into a loud failure."""
    import inspect

    import pydantic_ai.models as m

    assert "_first_chunk_monotonic" in inspect.getsource(m)


# ----- TimeLimitedModel: wall-clock max execution time ----------------


class _SlowModel(TestModel):
    """TestModel whose request never finishes within any sane budget."""

    async def request(self, *args, **kwargs):
        await asyncio.sleep(30)
        return await super().request(*args, **kwargs)


async def test_wall_clock_timeout_fires_on_stuck_request():
    model = TimeLimitedModel(TimedModel(_SlowModel()), wall_timeout=0.05)
    agent = Agent(model)
    with pytest.raises(LlmTimeoutError) as exc:
        await agent.run("hi")
    # The message must be UI-legible (it lands in session.error).
    assert "max execution time" in str(exc.value)
    assert "0.05s" in str(exc.value)


async def test_wall_clock_timeout_caps_stream_consumption():
    """A stream that keeps dribbling chunks forever must be cut off —
    the exact failure HTTP read timeouts cannot catch."""

    class _DribbleModel(TestModel):
        @asynccontextmanager
        async def request_stream(self, *args, **kwargs):
            async with super().request_stream(*args, **kwargs) as stream:
                yield stream
                await asyncio.sleep(30)  # server "keeps going" after chunks

    model = TimeLimitedModel(TimedModel(_DribbleModel()), wall_timeout=0.05)
    agent = Agent(model)

    async def handler(ctx, events):
        async for _ in events:
            pass

    with pytest.raises(LlmTimeoutError):
        await agent.run("hi", event_stream_handler=handler)


async def test_no_timeout_means_no_cap():
    model = TimeLimitedModel(TimedModel(TestModel(custom_output_text="ok")), wall_timeout=None)
    result = await Agent(model).run("hi")
    assert result.output


def test_ocr_timeout_falls_back_to_agent_profile(monkeypatch):
    """OCR without its own timeout inherits the agent's wall clock —
    same fallback family as endpoint/model/api_key."""
    from app.config import get_settings, reset_settings_cache
    from app.llm.factory import ocr_model

    monkeypatch.setenv("PLLM_LLM__AGENT__TIMEOUT_SECONDS", "123")
    reset_settings_cache()
    try:
        model, settings, _, _ = ocr_model()
        assert model.wall_timeout == 123.0
        assert settings["timeout"] == 123.0
    finally:
        reset_settings_cache()
