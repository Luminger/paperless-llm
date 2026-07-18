"""Read-only configuration overview.

Config stays file/env-driven — this endpoint only makes the effective
configuration inspectable. Secrets (API keys, tokens, webhook secret)
never leave the server; only their presence is reported.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.agents.registry import DEFAULT_BASE_PROMPT
from app.config import get_settings
from app.llm.ocr import OCR_PROMPT

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
