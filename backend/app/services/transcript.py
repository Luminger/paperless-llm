"""Derive a renderable chat transcript from a session's persisted
pydantic-ai message history.

The history JSON stays the single source of truth; this is a read-time
projection where EVERY part of the model exchange is a first-class,
explorable item: user prompts, thinking blocks, tool calls (full
arguments and full return values), and agent prose. Only system
prompts stay internal — they are configuration, not conversation.
Steering preambles travel as run-time ``instructions`` (see runner)
and therefore don't pollute user messages here.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel

_RESULT_LIMIT = 500

# The propose_* tool result format is OWNED by app.agents.tools._persist
# ("Proposal [[proposal:N]] (kind) recorded ..."): parsing it here is a
# contract between two modules of this codebase, not string guessing.
_PROPOSAL_TOKEN_RE = re.compile(r"\[\[proposal:(\d+)\]\]")


class CallTiming(BaseModel):
    """Per-LLM-call metrics, stamped by the TimedModel wrapper."""

    model_config = {"extra": "ignore"}

    started_at: str | None = None
    finished_at: str | None = None
    duration_s: float | None = None
    ttft_s: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tps: float | None = None


class TranscriptItem(BaseModel):
    role: Literal["user", "agent", "tool", "thinking"]
    content: str = ""
    # "pipeline" marks synthetic prompts the pipeline sent, so the UI can
    # render them muted instead of as a human utterance.
    origin: Literal["chat", "pipeline"] = "chat"
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    # Short form for the collapsed row …
    tool_result: str | None = None
    # … and the COMPLETE return value for the expanded view. The user
    # can audit exactly what the model got back.
    tool_result_full: Any = None
    tool_rejected: bool = False
    # For propose_* calls: the id of the proposal the call recorded, so
    # the UI can render the proposal card in place of the call.
    proposal_id: int | None = None
    # Per-LLM-call metrics. Every item derived from the same model
    # response shares that call's timing — a response with several tool
    # calls is still one LLM call.
    timing: CallTiming | None = None
    # Chronological anchor (part timestamp, else message timestamp) so
    # the UI can merge the transcript with session events by time.
    ts: str | None = None


def _text_of(content: Any) -> str:
    """User-prompt content may be a string or a multimodal list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c for c in content if isinstance(c, str))
    return str(content)


def _summarize(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            text = str(value)
    return text if len(text) <= _RESULT_LIMIT else text[: _RESULT_LIMIT] + " …[truncated]"


def _parse_args(args: Any) -> dict[str, Any] | None:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {"args": parsed}
        except ValueError:
            return {"args": args}
    return None


def derive_transcript(
    history: list[Any], *, pipeline_first_user: bool = False
) -> list[TranscriptItem]:
    """``pipeline_first_user``: the caller KNOWS whether this slice's
    first user prompt is synthetic (analysis kickoffs and auto
    continuations always are) — structural fact, not string matching."""
    items: list[TranscriptItem] = []
    # tool_call_id -> transcript item, to attach results to their calls.
    calls: dict[str, TranscriptItem] = {}
    first_user = True

    for message in history:
        if not isinstance(message, dict):
            continue
        timing: CallTiming | None = None
        details = message.get("provider_details")
        if isinstance(details, dict):
            raw_timing = details.get("pllm_timing")
            if isinstance(raw_timing, dict):
                timing = CallTiming(**{
                    k: v for k, v in raw_timing.items()
                    if k in CallTiming.model_fields
                })
        message_ts = message.get("timestamp")
        for part in message.get("parts") or []:
            if not isinstance(part, dict):
                continue
            kind = part.get("part_kind")
            ts = part.get("timestamp") or message_ts

            if kind == "user-prompt":
                text = _text_of(part.get("content"))
                is_pipeline = first_user and pipeline_first_user
                first_user = False
                items.append(
                    TranscriptItem(
                        role="user",
                        content=text,
                        origin="pipeline" if is_pipeline else "chat",
                        ts=ts,
                    )
                )
            elif kind == "text":
                text = str(part.get("content") or "").strip()
                if text:
                    items.append(
                        TranscriptItem(role="agent", content=text, timing=timing, ts=ts)
                    )
            elif kind == "tool-call":
                item = TranscriptItem(
                    role="tool",
                    tool_name=str(part.get("tool_name") or ""),
                    tool_args=_parse_args(part.get("args")),
                    timing=timing,
                    ts=ts,
                )
                items.append(item)
                if part.get("tool_call_id"):
                    calls[str(part["tool_call_id"])] = item
            elif kind == "tool-return":
                item = calls.get(str(part.get("tool_call_id")))
                if item is not None:
                    item.tool_result = _summarize(part.get("content"))
                    item.tool_result_full = part.get("content")
                    if (item.tool_name or "").startswith("propose_"):
                        m = _PROPOSAL_TOKEN_RE.search(str(part.get("content") or ""))
                        if m:
                            item.proposal_id = int(m.group(1))
            elif kind == "retry-prompt":
                item = calls.get(str(part.get("tool_call_id")))
                if item is not None:
                    item.tool_result = f"rejected: {_summarize(part.get('content'))}"
                    item.tool_result_full = part.get("content")
                    item.tool_rejected = True
            elif kind == "thinking":
                text = str(part.get("content") or "").strip()
                if text:
                    items.append(
                        TranscriptItem(
                            role="thinking", content=text, timing=timing, ts=ts
                        )
                    )
            # system-prompt: never surfaced (configuration, not
            # conversation).

    return items
