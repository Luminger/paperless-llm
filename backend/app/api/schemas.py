"""API request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.db.models import (
    AgentKind,
    EntityType,
    JobStatus,
    ProposalStatus,
    SessionPhase,
    SessionStatus,
)
from app.services.transcript import TranscriptItem


class SessionOut(BaseModel):
    id: int
    agent_kind: AgentKind
    entity_type: EntityType | None
    entity_id: int | None
    title: str
    status: SessionStatus
    phase: SessionPhase | None
    params: dict[str, Any] = {}
    error: str | None
    created_at: datetime
    updated_at: datetime
    proposal_count: int = 0

    model_config = {"from_attributes": True}


class RetryInfo(BaseModel):
    """State of the session's latest queue item — drives the retry UI."""

    state: str
    attempts: int
    max_attempts: int
    next_attempt_at: datetime | None
    # Chronological record of every finished attempt (never shadowed).
    history: list[dict[str, Any]] = []


class SessionDetailOut(SessionOut):
    transcript: list[TranscriptItem] = []
    proposals: list[ProposalOut] = []
    retry: RetryInfo | None = None


class ProposalOut(BaseModel):
    id: int
    session_id: int
    kind: str
    revision: int
    supersedes_id: int | None
    agent_payload: dict[str, Any]
    user_payload: dict[str, Any] | None
    status: ProposalStatus
    entity_type: EntityType | None
    entity_id: int | None
    created_at: datetime
    updated_at: datetime
    applied: bool = False
    reverted: bool = False

    model_config = {"from_attributes": True}


class ProposalPatch(BaseModel):
    user_payload: dict[str, Any] | None = None


class AnalyzeRequest(BaseModel):
    redo_ocr: bool = False
    instructions: str | None = None


class AnalyzeEntityRequest(BaseModel):
    instructions: str | None = None


class JobCreate(BaseModel):
    """Bulk campaign. Document set: explicit ids, a full-text query,
    the inbox, or all untagged documents."""

    document_ids: list[int] | None = None
    query: str | None = None
    inbox: bool = False
    untagged_only: bool = False
    redo_ocr: bool = False
    apply_policy: Literal["review", "auto"] = "review"
    instructions: str | None = None


class JobOut(BaseModel):
    id: int
    kind: str
    params: dict[str, Any] = {}
    status: JobStatus
    total: int
    done: int
    failed: int
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobDetailOut(JobOut):
    sessions: list[SessionOut] = []


class StatsOut(BaseModel):
    pending_proposals: int
    active_sessions: int
    queue_pending: dict[str, int]
    active_jobs: int


class MergeCandidateOut(BaseModel):
    entity_type: str
    source: dict[str, Any]
    target: dict[str, Any]
    string_score: float
    semantic_score: float | None


class OcrReviewOut(BaseModel):
    """Data for the OCR gate diff view. Deliberately excludes internal
    scoring (similarity) — the user reviews text, not metrics."""

    document_id: int
    previous_content: str
    ocr_text: str
    pages: int
    # Per-batch LLM call metrics of the OCR run.
    timings: list[dict[str, Any]] = []


class OcrGateRequest(BaseModel):
    # None -> keep the existing content; string -> accepted (possibly
    # hand-fixed in the diff view) content to write to paperless.
    content: str | None = None


class OcrRerunRequest(BaseModel):
    """Gate action: argue with the OCR — re-run it with instructions
    folded into the OCR prompt (and optionally a different render DPI)."""

    instructions: str | None = None
    dpi: int | None = None


class MessageRequest(BaseModel):
    content: str
