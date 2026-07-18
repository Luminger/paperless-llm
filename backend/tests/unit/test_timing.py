"""TimedModel: per-call metrics stamped into provider_details, surfaced
in the transcript."""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from app.llm.timing import TimedModel
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
