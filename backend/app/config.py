"""Application configuration.

Layered: defaults < TOML file (``PAPERLESS_LLM_CONFIG``, default
``./paperless-llm.toml``) < environment variables (``PLLM_`` prefix,
``__`` as nested delimiter, e.g. ``PLLM_LLM__AGENT__BASE_URL``).

Every serving-setup quirk (image limits, concurrency, streaming support,
thinking mode, sampling) is configuration here — never a hardcode.
See DESIGN.md "Model profiles".
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class SamplingOverrides(BaseModel):
    """Optional per-profile sampling overrides; unset values defer to the server."""

    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    presence_penalty: float | None = None


class AgentProfile(BaseModel):
    """Tool-calling chat model profile."""

    base_url: str = "http://127.0.0.1:8001/v1"
    model: str = "qwen3.6-27b"
    api_key: str = "unused"  # local endpoints usually ignore it; still sent
    # App-level semaphore across ALL requests to this endpoint (workers +
    # interactive lane combined). Size it below the server's max-num-seqs,
    # leaving room for other consumers of the same endpoint.
    max_concurrent: int = 2
    # Token-level streaming of model output. Keep off for servers with
    # buggy streaming tool-call parsers (e.g. vLLM qwen3_xml edge cases);
    # the UI then gets event-level SSE only.
    supports_streaming: bool = False
    # "server_default": don't send chat_template_kwargs at all.
    thinking: Literal["server_default", "on", "off"] = "server_default"
    # Used to clamp tool results (document content etc.), not enforced
    # against the server. Keep far below the server context window.
    max_input_tokens: int = 32768
    # Cap on agent tool-loop iterations (requests per run).
    max_tool_iterations: int = 12
    sampling: SamplingOverrides = SamplingOverrides()


class OcrProfile(BaseModel):
    """Vision/OCR profile. ``base_url``/``model`` fall back to the agent
    profile when unset, so a single-endpoint setup needs no extra config,
    while a dedicated OCR model (e.g. GLM-OCR) is a two-line change."""

    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    # Server-side multimodal limit, e.g. vLLM --limit-mm-per-prompt.
    max_images_per_request: int = 2
    # 0 = no limit.
    max_pages: int = 0
    render_dpi: int = 150
    sampling: SamplingOverrides = SamplingOverrides(temperature=0.1)
    # OCR results are cached keyed on (doc, checksum, model, prompt_version).
    prompt_version: int = 1


class EmbeddingsProfile(BaseModel):
    """Optional; configuring this enables all RAG features."""

    base_url: str = ""
    model: str = ""
    api_key: str = "unused"
    dimensions: int | None = None
    max_concurrent: int = 4

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model)


class RerankerProfile(BaseModel):
    """Optional Cohere-compatible /v1/rerank second stage."""

    base_url: str = ""
    model: str = ""
    api_key: str = "unused"

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model)


class LlmConfig(BaseModel):
    agent: AgentProfile = AgentProfile()
    ocr: OcrProfile = OcrProfile()
    embeddings: EmbeddingsProfile = EmbeddingsProfile()
    reranker: RerankerProfile = RerankerProfile()


class PaperlessConfig(BaseModel):
    base_url: str = "http://127.0.0.1:8000"
    # Either a token, or username/password (a token is then fetched
    # lazily via /api/token/ — handy for throwaway instances).
    token: str = ""
    username: str = ""
    password: str = ""
    timeout_seconds: float = 30.0


class WebhookConfig(BaseModel):
    # Shared secret expected in the X-PLLM-Token header of webhook ingress.
    # Empty disables the webhook endpoint entirely.
    secret: str = ""
    # Defaults for sessions created via webhook ingress.
    redo_ocr: bool = False
    apply_policy: Literal["review", "auto"] = "review"


class QueueConfig(BaseModel):
    """In-process worker pool over the persistent DB queue. Two lanes:
    interactive (chat turns, single analyses) and batch (campaigns).
    Concurrency here multiplies against the LLM semaphores — the model
    endpoint's max_concurrent is the real global cap."""

    interactive_concurrency: int = 2
    batch_concurrency: int = 2
    poll_interval_seconds: float = 1.0
    # Automatic retries for failed stages (LLM hiccups, restarts, …):
    # a failed stage is re-run up to retry_attempts more times, waiting
    # retry_delay_seconds between attempts. "Retry now" in the UI
    # overrides the wait (and revives exhausted stages).
    retry_attempts: int = 2
    retry_delay_seconds: float = 60.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PLLM_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    llm: LlmConfig = LlmConfig()
    paperless: PaperlessConfig = PaperlessConfig()
    webhook: WebhookConfig = WebhookConfig()
    queue: QueueConfig = QueueConfig()

    database_url: str = "sqlite+aiosqlite:///./data/paperless_llm.sqlite3"
    # Where OCR page renders / caches live.
    data_dir: Path = Path("./data")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority (first wins): init kwargs > env > TOML file > defaults.
        return (init_settings, env_settings, _TomlSource(settings_cls))


class _TomlSource(PydanticBaseSettingsSource):
    """Reads the TOML config file named by PAPERLESS_LLM_CONFIG."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._data: dict = {}
        path = Path(os.environ.get("PAPERLESS_LLM_CONFIG", "paperless-llm.toml"))
        if path.is_file():
            self._data = tomllib.loads(path.read_text())

    def get_field_value(self, field, field_name):  # type: ignore[override]
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict:
        return dict(self._data)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """For tests."""
    get_settings.cache_clear()
