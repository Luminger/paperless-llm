"""Transcript derivation: the persisted pydantic-ai history is the
source of truth; the transcript is a filtered read-time projection."""

from __future__ import annotations

from app.services.transcript import derive_transcript


def _request(*parts, instructions=None):
    return {"kind": "request", "parts": list(parts), "instructions": instructions}


def _response(*parts):
    return {"kind": "response", "parts": list(parts)}


def test_pipeline_prompt_marked_and_chat_kept_apart():
    history = [
        _request(
            {"part_kind": "system-prompt", "content": "SECRET SYSTEM"},
            {"part_kind": "user-prompt", "content": "Process document id=7."},
        ),
        _response({"part_kind": "text", "content": "Done, proposed a title."}),
        _request({"part_kind": "user-prompt", "content": "Use German instead"}),
        _response({"part_kind": "text", "content": "Revised."}),
    ]
    # The CALLER declares synthetic kickoffs (structural fact from the
    # step kind) — no string matching on prompt wording.
    t = derive_transcript(history, pipeline_first_user=True)
    assert [(i.role, i.origin) for i in t if i.role == "user"] == [
        ("user", "pipeline"),
        ("user", "chat"),
    ]
    assert [i.content for i in t if i.role == "agent"] == [
        "Done, proposed a title.",
        "Revised.",
    ]
    # System prompts never surface.
    assert all("SECRET" not in i.content for i in t)


def test_tool_calls_paired_with_results_and_retries():
    history = [
        _request({"part_kind": "user-prompt", "content": "go"}),
        _response(
            {
                "part_kind": "tool-call",
                "tool_name": "get_document",
                "args": '{"document_id": 7}',
                "tool_call_id": "c1",
            },
            {
                "part_kind": "tool-call",
                "tool_name": "propose_update_document_metadata",
                "args": {"document_id": 7, "title": "x"},
                "tool_call_id": "c2",
            },
        ),
        _request(
            {"part_kind": "tool-return", "tool_call_id": "c1", "content": {"id": 7}},
            {
                "part_kind": "retry-prompt",
                "tool_call_id": "c2",
                "content": "no-op proposal",
            },
        ),
        _response({"part_kind": "text", "content": "done"}),
    ]
    t = derive_transcript(history)
    tools = [i for i in t if i.role == "tool"]
    assert tools[0].tool_name == "get_document"
    assert tools[0].tool_args == {"document_id": 7}  # JSON string parsed
    assert tools[0].tool_result == '{"id": 7}'
    assert tools[1].tool_args == {"document_id": 7, "title": "x"}
    assert tools[1].tool_result.startswith("rejected: ")


def test_propose_result_yields_structural_proposal_id():
    history = [
        _response(
            {
                "part_kind": "tool-call",
                "tool_name": "propose_update_document_metadata",
                "args": {},
                "tool_call_id": "c1",
            },
        ),
        _request(
            {
                "part_kind": "tool-return",
                "tool_call_id": "c1",
                "content": "Proposal [[proposal:42]] (update_document_metadata) recorded for human review.",
            }
        ),
    ]
    t = derive_transcript(history)
    tool = next(i for i in t if i.role == "tool")
    assert tool.proposal_id == 42


def test_thinking_surfaced_and_full_results_kept():
    """Thinking blocks are first-class transcript items; tool results
    keep a truncated summary for the collapsed row AND the complete
    value for the expanded view."""
    history = [
        _request({"part_kind": "user-prompt", "content": "go"}),
        _response(
            {"part_kind": "thinking", "content": "internal chain of thought"},
            {
                "part_kind": "tool-call",
                "tool_name": "search_documents",
                "args": {},
                "tool_call_id": "c1",
            },
        ),
        _request(
            {"part_kind": "tool-return", "tool_call_id": "c1", "content": "x" * 2000}
        ),
    ]
    t = derive_transcript(history)
    thinking = next(i for i in t if i.role == "thinking")
    assert thinking.content == "internal chain of thought"
    tool = next(i for i in t if i.role == "tool")
    assert len(tool.tool_result) < 600
    assert tool.tool_result.endswith("…[truncated]")
    assert tool.tool_result_full == "x" * 2000  # nothing lost
    assert tool.tool_rejected is False


def test_system_prompt_stays_internal():
    history = [
        _request({"part_kind": "system-prompt", "content": "secret sauce"}),
        _request({"part_kind": "user-prompt", "content": "go"}),
    ]
    t = derive_transcript(history)
    assert all("secret sauce" not in (i.content or "") for i in t)


def test_multimodal_user_content_and_garbage_tolerated():
    history = [
        _request({"part_kind": "user-prompt", "content": ["look at this", {"blob": 1}]}),
        "not-a-dict",
        {"kind": "response"},  # no parts
    ]
    t = derive_transcript(history)
    assert t[0].role == "user" and t[0].content == "look at this"
