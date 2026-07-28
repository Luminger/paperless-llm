"""Configuration: the read-only effective overview, plus the runtime
override layer the Settings UI edits (whitelisted keys, admin-only,
never able to shadow the environment). Secrets (API keys, tokens,
webhook secret) never leave the server; only their presence is
reported.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict
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
from app.llm import diagnostics
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
    # The URL paperless posts to (webhook.public_url); empty = not set.
    public_url: str = ""
    # None = this paperless doesn't expose the workflows API (or the
    # app's credentials can't read it) — honestly unknown.
    workflow_found: bool | None = None
    workflow_name: str = ""
    workflow_enabled: bool = True
    # Whether the workflow's CONTENT matches the current settings (URL,
    # secret header, payload shape, trigger) — a workflow can exist and
    # still post old values somewhere else. None = not judgeable
    # (public_url/secret unset, or no workflow).
    workflow_synced: bool | None = None
    # What exactly drifted, for the UI: "url", "secret", "payload", "trigger".
    workflow_drift: list[str] = []
    # Where the super administrator manages workflows in paperless.
    workflows_url: str


_WEBHOOK_PATH = "/api/webhooks/paperless"
_WORKFLOW_NAME = "paperless-llm: analyze new documents"


def _find_webhook_workflow(flows: list[dict]) -> dict | None:
    """Version-tolerant: a webhook action carries our URL somewhere in
    its serialized form."""
    for flow in flows:
        if _WEBHOOK_PATH in json.dumps(flow.get("actions", [])):
            return flow
    return None


def _workflow_drift(flow: dict, public_url: str, secret: str) -> list[str]:
    """Compare the workflow's ACTUAL content against what the one-click
    setup would write today. Existence is not sync: after a public_url
    or secret change the workflow still posts the OLD values until it
    is healed."""
    hook: dict | None = None
    for action in flow.get("actions") or []:
        wh = action.get("webhook") or {}
        if _WEBHOOK_PATH in str(wh.get("url", "")):
            hook = wh
            break
    if hook is None:
        # Matched only via the serialized dump — a shape this code
        # doesn't know. Treat as drift so the heal path runs.
        return ["unreadable"]
    drift: list[str] = []
    if hook.get("url") != f"{public_url.rstrip('/')}{_WEBHOOK_PATH}":
        drift.append("url")
    if (hook.get("headers") or {}).get("X-PLLM-Token") != secret:
        drift.append("secret")
    if (hook.get("params") or {}).get("url") != "{doc_url}" or not hook.get("as_json"):
        drift.append("payload")
    if not any(
        t.get("type") == 2 for t in flow.get("triggers") or []
    ):
        drift.append("trigger")
    return drift


@router.get("/settings/webhook")
async def webhook_status(
    paperless: PaperlessClient = Depends(get_paperless),
) -> WebhookStatusOut:
    s = get_settings()
    external = (s.paperless.external_url or s.paperless.base_url).rstrip("/")
    out = WebhookStatusOut(
        secret_configured=bool(s.webhook.secret),
        public_url=s.webhook.public_url,
        workflows_url=f"{external}/workflows",
    )
    try:
        flows = await paperless.list_workflows()
    except Exception:  # noqa: BLE001 — older paperless / missing permission
        return out
    flow = _find_webhook_workflow(flows)
    out.workflow_found = flow is not None
    if flow is not None:
        out.workflow_name = str(flow.get("name", ""))
        out.workflow_enabled = bool(flow.get("enabled", True))
        # Only judge sync when the expected values exist app-side.
        if s.webhook.public_url and s.webhook.secret:
            out.workflow_drift = _workflow_drift(
                flow, s.webhook.public_url, s.webhook.secret
            )
            out.workflow_synced = not out.workflow_drift
    return out



class WebhookSetupOut(BaseModel):
    ok: bool
    created: bool = False  # False on ok=True means an existing workflow was updated
    workflow_id: int | None = None
    workflow_name: str = ""
    secret_generated: bool = False
    message: str


@router.post("/settings/webhook/setup")
async def webhook_setup(
    db: AsyncSession = Depends(get_session),
    paperless: PaperlessClient = Depends(get_paperless),
    user: CurrentUser = Depends(require_admin),
) -> WebhookSetupOut:
    """One-click ingress: generate a secret when none exists (runtime
    override), then create — or fix up — the paperless workflow that
    posts new documents to this app. Requires webhook.public_url."""
    cfg = get_settings().webhook
    if not cfg.public_url:
        return WebhookSetupOut(
            ok=False,
            message="webhook.public_url is not set — configure the URL this "
            "app is reachable at from paperless first",
        )
    secret_generated = False
    secret = cfg.secret
    if not secret:
        if "webhook.secret" in env_provided_keys():
            return WebhookSetupOut(
                ok=False,
                message="webhook.secret is locked (empty) by the environment "
                "— set PLLM_WEBHOOK__SECRET to a value first",
            )
        secret = secrets.token_urlsafe(32)
        merged = runtime_overrides()
        merged["webhook.secret"] = secret
        set_runtime_overrides(merged)
        get_settings()  # revalidate with the new layer active
        await save_overrides(db, merged)
        secret_generated = True

    url = f"{cfg.public_url.rstrip('/')}{_WEBHOOK_PATH}"
    payload = {
        "name": _WORKFLOW_NAME,
        "order": 0,
        "enabled": True,
        # Trigger type 2 = Document Added (post-consumption — content and
        # metadata exist); sources: consume folder, API upload, mail.
        "triggers": [{"type": 2, "sources": [1, 2, 3]}],
        "actions": [
            {
                "type": 4,  # webhook
                "webhook": {
                    "url": url,
                    "use_params": True,
                    "as_json": True,
                    # {doc_url} is the only id-bearing placeholder the
                    # webhook action offers; our ingress parses the id
                    # out of it (_extract_document_ids).
                    "params": {"url": "{doc_url}"},
                    "headers": {"X-PLLM-Token": secret},
                    "include_document": False,
                },
            }
        ],
    }
    try:
        flows = await paperless.list_workflows()
        existing = _find_webhook_workflow(flows)
        if existing is not None:
            # Keep the user's name; replace triggers/actions wholesale so
            # a stale URL or secret is healed.
            payload["name"] = str(existing.get("name") or _WORKFLOW_NAME)
            payload["order"] = existing.get("order", 0)
            flow = await paperless.update_workflow(int(existing["id"]), payload)
            created = False
        else:
            flow = await paperless.create_workflow(payload)
            created = True
    except Exception as e:  # noqa: BLE001 — surface, don't 500
        return WebhookSetupOut(
            ok=False,
            secret_generated=secret_generated,
            message=f"paperless rejected the workflow: {e} — does the app's "
            "account have workflow permissions (superuser)?",
        )
    await record(
        db, "webhook", "workflow_created" if created else "workflow_updated",
        workflow_id=flow.get("id"), url=url, user=user.name,
        secret_generated=secret_generated,
    )
    await db.commit()
    return WebhookSetupOut(
        ok=True,
        created=created,
        workflow_id=flow.get("id"),
        workflow_name=str(payload["name"]),
        secret_generated=secret_generated,
        message=(
            "workflow created — new documents now flow to this app"
            if created
            else "existing workflow updated (URL and secret refreshed)"
        ),
    )


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
    # Via the module so tests can monkeypatch diagnostics.run_llm_test.
    return LlmTestOut(**asdict(await diagnostics.run_llm_test(profile)))


@router.post("/settings/llm/{profile}/detect")
async def llm_capability_detect(
    profile: Literal["agent", "ocr"],
    user: CurrentUser = Depends(require_admin),
) -> LlmDetectOut:
    """Detect the server's context window (agent) / images-per-request
    limit (ocr, via an empirical probe with tiny images)."""
    return LlmDetectOut(**asdict(await diagnostics.run_llm_detect(profile)))


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
