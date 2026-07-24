"""Configuration: the read-only effective overview, plus the runtime
override layer the Settings UI edits (whitelisted keys, admin-only,
never able to shadow the environment). Secrets (API keys, tokens,
webhook secret) never leave the server; only their presence is
reported.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import DEFAULT_BASE_PROMPT
from app.api.deps import get_paperless, require_admin
from app.config import (
    EDITABLE_KEYS,
    Settings,
    env_provided_keys,
    file_provided_keys,
    get_settings,
    is_secret_key,
    runtime_overrides,
    set_runtime_overrides,
)
from app.db.session import get_session
from app.llm.ocr import OCR_PROMPT
from app.paperless import PaperlessClient
from app.services.audit import record
from app.services.auth import CurrentUser
from app.services.runtime_config import save_overrides

router = APIRouter(prefix="/api", tags=["settings"])


class ProfileOut(BaseModel):
    configured: bool = True
    base_url: str = ""
    model: str = ""
    max_concurrent: int | None = None
    supports_streaming: bool | None = None
    thinking: str | None = None
    max_input_tokens: int | None = None
    max_tool_iterations: int | None = None


class PaperlessOut(BaseModel):
    base_url: str
    external_url: str
    auth: str  # "token" | "credentials" | "none"
    timeout_seconds: float
    verify_tls: bool = True


class QueueOut(BaseModel):
    interactive_concurrency: int
    batch_concurrency: int
    retry_attempts: int
    retry_delay_seconds: float


class WebhookOut(BaseModel):
    enabled: bool
    redo_ocr: bool
    apply_policy: str


class PromptDefaults(BaseModel):
    agent_base: str
    ocr_base: str


class SettingsOut(BaseModel):
    llm_agent: ProfileOut
    llm_ocr: ProfileOut
    llm_embeddings: ProfileOut
    llm_reranker: ProfileOut
    paperless: PaperlessOut
    queue: QueueOut
    webhook: WebhookOut
    database: str  # backend only (sqlite/postgresql), never the DSN
    # System-supplied prompt bases (the Settings UI shows them as the
    # reset/default state for user tweaking).
    prompt_defaults: PromptDefaults


@router.get("/settings")
async def get_settings_overview() -> SettingsOut:
    s = get_settings()
    agent = s.llm.agent
    ocr = s.llm.ocr
    emb = s.llm.embeddings
    rer = s.llm.reranker
    return SettingsOut(
        llm_agent=ProfileOut(
            base_url=agent.base_url,
            model=agent.model,
            max_concurrent=agent.max_concurrent,
            supports_streaming=agent.supports_streaming,
            thinking=agent.thinking,
            max_input_tokens=agent.max_input_tokens,
            max_tool_iterations=agent.max_tool_iterations,
        ),
        llm_ocr=ProfileOut(
            # OCR falls back to the agent endpoint when unset.
            configured=bool(ocr.base_url or ocr.model),
            base_url=ocr.base_url or agent.base_url,
            model=ocr.model or agent.model,
        ),
        llm_embeddings=ProfileOut(
            configured=emb.enabled,
            base_url=emb.base_url,
            model=emb.model,
            max_concurrent=emb.max_concurrent,
        ),
        llm_reranker=ProfileOut(
            configured=rer.enabled,
            base_url=rer.base_url,
            model=rer.model,
        ),
        paperless=PaperlessOut(
            base_url=s.paperless.base_url,
            external_url=s.paperless.external_url or s.paperless.base_url,
            auth="token"
            if s.paperless.token
            else "credentials"
            if s.paperless.username
            else "none",
            timeout_seconds=s.paperless.timeout_seconds,
            verify_tls=s.paperless.verify_tls,
        ),
        queue=QueueOut(
            interactive_concurrency=s.queue.interactive_concurrency,
            batch_concurrency=s.queue.batch_concurrency,
            retry_attempts=s.queue.retry_attempts,
            retry_delay_seconds=s.queue.retry_delay_seconds,
        ),
        webhook=WebhookOut(
            enabled=bool(s.webhook.secret),
            redo_ocr=s.webhook.redo_ocr,
            apply_policy=s.webhook.apply_policy,
        ),
        database=s.database_url.split(":", 1)[0].split("+", 1)[0],
        prompt_defaults=PromptDefaults(
            agent_base=DEFAULT_BASE_PROMPT, ocr_base=OCR_PROMPT
        ),
    )


class WebhookStatusOut(BaseModel):
    """Deterministic webhook state: the app side (secret configured)
    AND the paperless side (a workflow that actually posts to us)."""

    secret_configured: bool
    # None = this paperless doesn't expose the workflows API (or the
    # app's credentials can't read it) — honestly unknown.
    workflow_found: bool | None = None
    workflow_name: str = ""
    workflow_enabled: bool = True
    # Where the super administrator manages workflows in paperless.
    workflows_url: str


@router.get("/settings/webhook")
async def webhook_status(
    paperless: PaperlessClient = Depends(get_paperless),
) -> WebhookStatusOut:
    import json as _json

    s = get_settings()
    external = (s.paperless.external_url or s.paperless.base_url).rstrip("/")
    out = WebhookStatusOut(
        secret_configured=bool(s.webhook.secret),
        workflows_url=f"{external}/workflows",
    )
    try:
        flows = await paperless.list_workflows()
    except Exception:  # noqa: BLE001 — older paperless / missing permission
        return out
    out.workflow_found = False
    for flow in flows:
        # Version-tolerant: a webhook action carries our URL somewhere in
        # its serialized form.
        if "/api/webhooks/paperless" in _json.dumps(flow.get("actions", [])):
            out.workflow_found = True
            out.workflow_name = str(flow.get("name", ""))
            out.workflow_enabled = bool(flow.get("enabled", True))
            break
    return out


# ----- LLM diagnostics -------------------------------------------------


class LlmTestOut(BaseModel):
    """One real completion against the profile's endpoint — the vision
    profile is tested WITH an image attached."""

    ok: bool
    base_url: str
    model: str
    latency_ms: int | None = None
    reply: str | None = None
    error: str | None = None


class LlmDetectOut(BaseModel):
    """Best-effort capability detection. ``suggestions`` maps runtime-
    editable config keys to detected values — the UI fills them into
    the form for review, nothing is saved server-side."""

    base_url: str
    model: str
    context_length: int | None = None
    context_source: str | None = None
    max_images: int | None = None
    # False = probed up to the ceiling without hitting a server cap.
    max_images_exact: bool | None = None
    # Measured page cost (one blank A4 at render_dpi, usage-reported)
    # and the resulting context-fit prediction.
    render_dpi: int | None = None
    tokens_per_image: int | None = None
    images_in_context: int | None = None
    error: str | None = None
    suggestions: dict[str, int] = {}


@router.post("/settings/llm/{profile}/test")
async def llm_connectivity_test(
    profile: Literal["agent", "ocr", "embeddings", "reranker"],
    user: CurrentUser = Depends(require_admin),
) -> LlmTestOut:
    """Fire one tiny call at the configured endpoint (admin-only: it
    spends real tokens). The OCR profile resolves its agent-profile
    fallback exactly like production calls do; embeddings and reranker
    go through their production client code paths."""
    from dataclasses import asdict

    from app.llm.diagnostics import run_llm_test

    return LlmTestOut(**asdict(await run_llm_test(profile)))


@router.post("/settings/llm/{profile}/detect")
async def llm_capability_detect(
    profile: Literal["agent", "ocr"],
    user: CurrentUser = Depends(require_admin),
) -> LlmDetectOut:
    """Detect the server's context window (agent) / images-per-request
    limit (ocr, via an empirical probe with tiny images)."""
    from dataclasses import asdict

    from app.llm.diagnostics import run_llm_detect

    return LlmDetectOut(**asdict(await run_llm_detect(profile)))


# ----- the runtime override layer --------------------------------------


class ConfigRowOut(BaseModel):
    key: str
    # Masked to None for secret keys — is_set still tells the truth.
    value: Any = None
    is_set: bool = False
    secret: bool = False
    # Where the effective value comes from; env-sourced keys are locked.
    source: Literal["environment", "ui", "file", "default"]
    editable: bool


class ConfigUpdate(BaseModel):
    # dotted key -> new value; null clears the UI override.
    values: dict[str, Any]


def _effective(s: Settings, dotted: str) -> Any:
    node: Any = s
    for part in dotted.split("."):
        node = getattr(node, part)
    return node


def _config_rows() -> list[ConfigRowOut]:
    s = get_settings()
    env_keys = env_provided_keys()
    file_keys = file_provided_keys()
    overrides = runtime_overrides()
    rows = []
    for key in EDITABLE_KEYS:
        value = _effective(s, key)
        secret = is_secret_key(key)
        source = (
            "environment"
            if key in env_keys
            else "ui"
            if key in overrides
            else "file"
            if key in file_keys
            else "default"
        )
        rows.append(
            ConfigRowOut(
                key=key,
                value=None if secret else value,
                is_set=bool(value) if secret else value is not None,
                secret=secret,
                source=source,
                editable=key not in env_keys,
            )
        )
    return rows


@router.get("/settings/config")
async def get_config() -> list[ConfigRowOut]:
    """The UI-editable slice of the configuration, with per-key source
    and lock state."""
    return _config_rows()


@router.put("/settings/config")
async def put_config(
    body: ConfigUpdate,
    db: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_admin),
) -> list[ConfigRowOut]:
    env_keys = env_provided_keys()
    for key in body.values:
        if key not in EDITABLE_KEYS:
            raise HTTPException(
                422, {"code": "not_editable", "message": f"{key!r} is not runtime-editable"}
            )
        if key in env_keys:
            raise HTTPException(
                409,
                {
                    "code": "locked_by_environment",
                    "message": f"{key!r} is set by the environment and cannot be "
                    "overridden at runtime",
                },
            )
    merged = runtime_overrides()
    for key, value in body.values.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    # Validate the WHOLE settings object with the new layer active — a
    # bad value must never become the running config.
    previous = runtime_overrides()
    set_runtime_overrides(merged)
    try:
        get_settings()
    except ValidationError as e:
        set_runtime_overrides(previous)
        raise HTTPException(
            422,
            {"code": "invalid_value", "message": str(e.errors()[0].get("msg", e))},
        ) from e
    await save_overrides(db, merged)
    await record(
        db, "config", "updated",
        keys=sorted(body.values), user=user.name,
    )
    await db.commit()
    return _config_rows()
