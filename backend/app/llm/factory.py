"""Model factory: turns config profiles into pydantic-ai models.

All serving-setup quirks flow from config (DESIGN.md "Model profiles");
nothing here assumes a particular server beyond OpenAI compatibility.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from app.config import AgentProfile, OcrProfile, SamplingOverrides, get_settings
from app.llm.timing import TimedModel

# One semaphore per endpoint URL, shared by every consumer in this
# process (agent runs, OCR, interactive chat). Sized via config
# `max_concurrent` to respect server-side limits (e.g. vLLM max-num-seqs
# shared with other services).
_semaphores: dict[str, asyncio.Semaphore] = {}


def llm_semaphore(base_url: str, max_concurrent: int) -> asyncio.Semaphore:
    key = f"{base_url}#{max_concurrent}"
    if key not in _semaphores:
        _semaphores[key] = asyncio.Semaphore(max_concurrent)
    return _semaphores[key]


def _settings_from(sampling: SamplingOverrides, thinking: str = "server_default") -> ModelSettings:
    settings: dict[str, Any] = {}
    if sampling.temperature is not None:
        settings["temperature"] = sampling.temperature
    if sampling.top_p is not None:
        settings["top_p"] = sampling.top_p
    if sampling.max_tokens is not None:
        settings["max_tokens"] = sampling.max_tokens
    if sampling.presence_penalty is not None:
        settings["presence_penalty"] = sampling.presence_penalty
    if thinking != "server_default":
        # vLLM/SGLang-style opt-in/out; harmless elsewhere.
        settings["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": thinking == "on"}
        }
    return ModelSettings(**settings)  # type: ignore[typeddict-item]


def _build_model(base_url: str, model: str, api_key: str) -> Model:
    # TimedModel stamps per-call metrics (duration, tokens, tps, ttft
    # when streaming) into each response's provider_details.
    return TimedModel(
        OpenAIChatModel(model, provider=OpenAIProvider(base_url=base_url, api_key=api_key))
    )


def agent_model(profile: AgentProfile | None = None) -> Model:
    p = profile or get_settings().llm.agent
    return _build_model(p.base_url, p.model, p.api_key)


def agent_model_settings(profile: AgentProfile | None = None) -> ModelSettings:
    p = profile or get_settings().llm.agent
    return _settings_from(p.sampling, p.thinking)


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
    sem = llm_semaphore(base_url, get_settings().llm.agent.max_concurrent)
    # OCR is a plain completion; thinking adds latency for no benefit.
    return _build_model(base_url, model, api_key), _settings_from(ocr.sampling, "off"), ocr, sem
