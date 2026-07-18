"""Application configuration.

Layered (first wins): environment variables (``PLLM_`` prefix, ``__``
as nested delimiter) > runtime overrides set in the Settings UI
(DB-persisted, whitelisted keys only) > TOML file
(``PAPERLESS_LLM_CONFIG``, default ``./paperless-llm.toml``) >
defaults.

Environment variables are authoritative: a key set there cannot be
overridden by the config file (the startup log warns about the
shadowed value) or the UI (the key shows as locked). Only a curated
whitelist is UI-editable at all — anything whose misconfiguration
could brick the app (paperless connection, database, auth) stays
file/env-only, because a broken value there would take down the very
UI needed to fix it.

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
    # URL users reach paperless under (deep links in the UI); falls back
    # to base_url when unset.
    external_url: str = ""
    # Either a token, or username/password (a token is then fetched
    # lazily via /api/token/ — handy for throwaway instances).
    token: str = ""
    username: str = ""
    password: str = ""
    timeout_seconds: float = 30.0
    # TLS certificate/host verification for the paperless connection.
    # On by default; turning it off is for self-signed setups and is
    # deliberately NOT UI-editable (config/env only).
    verify_tls: bool = True


class AuthConfig(BaseModel):
    """Who may use the app (see DESIGN.md "Authentication").

    ONE auth story: the login form is validated against paperless
    itself (``POST /api/token/``) — no user store of our own, no mode
    matrix. The per-user paperless token performs that user's applied
    changes, so paperless's own audit trail names the real person.
    """

    # Signed session cookie lifetime.
    session_hours: int = 24 * 7
    # HMAC secret for the session cookie. Empty = generated once and
    # persisted app-side (survives restarts).
    session_secret: str = ""


class WebhookConfig(BaseModel):
    # Shared secret expected in the X-PLLM-Token header of webhook ingress.
    # Empty disables the webhook endpoint entirely.
    secret: str = ""
    # Defaults for sessions created via webhook ingress.
    redo_ocr: bool = False
    apply_policy: Literal["review", "auto"] = "review"


class QueueConfig(BaseModel):
    """In-process worker pool over the persistent DB queue. Two lanes:
    interactive (chat turns, single analyses) and batch (bulk jobs).
    Concurrency here multiplies against the LLM semaphores — the model
    endpoint's max_concurrent is the real global cap."""

    interactive_concurrency: int = 2
    batch_concurrency: int = 2
    poll_interval_seconds: float = 1.0
    # Runaway brake for autonomous (auto-apply) sessions: at most this
    # many auto-continuation turns per session. Manual continuations
    # (user applies) are user-driven and never limited.
    auto_continuation_limit: int = 10
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
    auth: AuthConfig = AuthConfig()
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
        # Priority (first wins): init kwargs > env > UI overrides (DB)
        # > TOML file > defaults.
        return (
            init_settings,
            env_settings,
            _OverridesSource(settings_cls),
            _TomlSource(settings_cls),
        )


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


# ----- runtime overrides (the Settings UI's layer) ---------------------

# Dotted key -> raw value, e.g. {"llm.agent.model": "qwen3.6-27b"}.
# Loaded from the DB at startup, replaced on every UI save.
_runtime_overrides: dict[str, object] = {}


def set_runtime_overrides(values: dict[str, object]) -> None:
    global _runtime_overrides
    _runtime_overrides = dict(values)
    reset_settings_cache()


def runtime_overrides() -> dict[str, object]:
    return dict(_runtime_overrides)


def _nest(flat: dict[str, object]) -> dict:
    out: dict = {}
    for dotted, value in flat.items():
        node = out
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def _flatten(data: dict, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for k, v in data.items():
        dotted = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, f"{dotted}."))
        else:
            out[dotted] = v
    return out


class _OverridesSource(PydanticBaseSettingsSource):
    """UI-set values, below env, above the config file."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._data = _nest(_runtime_overrides)

    def get_field_value(self, field, field_name):  # type: ignore[override]
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict:
        return dict(self._data)


# ----- source inspection (who set what) --------------------------------


def env_provided_keys() -> set[str]:
    """Dotted keys the environment sets (PLLM_LLM__AGENT__MODEL ->
    "llm.agent.model"). These are locked everywhere else."""
    out = set()
    for k in os.environ:
        if k.startswith("PLLM_") and k != "PLLM_":
            out.add(k[len("PLLM_"):].lower().replace("__", "."))
    return out


def file_provided_keys() -> set[str]:
    return set(_flatten(_TomlSource(Settings)._data))


def warn_env_file_collisions() -> list[str]:
    """Precedence is environment > config file; when the file sets a key
    the environment also sets, the file value silently loses — except it
    must not be silent. Called at startup."""
    import logging

    shadowed = sorted(env_provided_keys() & file_provided_keys())
    for key in shadowed:
        logging.getLogger(__name__).warning(
            "config file value for %r is ignored — the environment variable "
            "sets it (precedence: environment > settings UI > config file)",
            key,
        )
    return shadowed


# ----- the UI-editable whitelist ---------------------------------------

# Keys the Settings UI may override at runtime. Deliberately excludes
# everything whose misconfiguration would take the app down with no way
# to recover from the UI (paperless connection, database, auth, worker
# pool sizes fixed at startup).
EDITABLE_KEYS: tuple[str, ...] = (
    "llm.agent.base_url",
    "llm.agent.model",
    "llm.agent.api_key",
    "llm.agent.max_concurrent",
    "llm.agent.supports_streaming",
    "llm.agent.thinking",
    "llm.agent.max_input_tokens",
    "llm.agent.max_tool_iterations",
    "llm.ocr.base_url",
    "llm.ocr.model",
    "llm.ocr.api_key",
    "llm.ocr.max_images_per_request",
    "llm.ocr.max_pages",
    "llm.ocr.render_dpi",
    "llm.embeddings.base_url",
    "llm.embeddings.model",
    "llm.embeddings.api_key",
    "llm.reranker.base_url",
    "llm.reranker.model",
    "llm.reranker.api_key",
    "queue.auto_continuation_limit",
    "webhook.redo_ocr",
    "webhook.apply_policy",
)


def is_secret_key(key: str) -> bool:
    return key.rsplit(".", 1)[-1] in ("api_key", "token", "password", "secret")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """For tests and after runtime-override changes."""
    get_settings.cache_clear()
