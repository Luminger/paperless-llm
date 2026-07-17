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
    QueueLane,
    SessionPhase,
    SessionStatus,
    StepKind,
    StepState,
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
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    proposal_count: int = 0

    model_config = {"from_attributes": True}


class SessionPage(BaseModel):
    """Generic pagination envelope for session lists."""

    count: int
    page: int
    page_size: int
    results: list[SessionOut]


class StepOut(BaseModel):
    """One timeline element. Generic frame data (state, attempts,
    scheduling, timestamps) is uniform across kinds; ``input`` and
    ``result`` are kind-specific; agent-turn steps carry their
    transcript slice."""

    id: int
    session_id: int
    kind: StepKind
    state: StepState
    lane: QueueLane
    input: dict[str, Any] = {}
    result: dict[str, Any] = {}
    error: str | None
    attempts: list[dict[str, Any]] = []
    attempt_count: int
    max_attempts: int
    scheduled_at: datetime | None
    supersedes_id: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    transcript: list[TranscriptItem] = []

    model_config = {"from_attributes": True}


class SessionDetailOut(SessionOut):
    steps: list[StepOut] = []
    proposals: list[ProposalOut] = []


class ProposalOut(BaseModel):
    id: int
    session_id: int
    kind: str
    revision: int
    supersedes_id: int | None
    agent_payload: dict[str, Any]
    user_payload: dict[str, Any] | None
    # Paperless values of the touched fields at proposal time.
    base_snapshot: dict[str, Any] | None = None
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
    # Lifetime counters: ocr_runs, ocr_pages, llm_requests,
    # llm_input_tokens, llm_output_tokens.
    lifetime: dict[str, int] = {}


class AuditEntryOut(BaseModel):
    id: int
    ts: datetime
    kind: str
    action: str
    actor: str = "system"
    detail: dict[str, Any] = {}


class AuditPage(BaseModel):
    count: int
    page: int
    page_size: int
    results: list[AuditEntryOut]


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


class ResolveRequest(BaseModel):
    """Resolution body for awaiting_user steps. OCR gate: None keeps the
    existing content; a string is the accepted (possibly hand-fixed)
    text."""

    content: str | None = None


class RedoRequest(BaseModel):
    """Amended input for a redo (merged over the original step input) —
    e.g. {"instructions": "..."} for an OCR re-run."""

    input: dict[str, Any] | None = None


class MessageRequest(BaseModel):
    content: str
