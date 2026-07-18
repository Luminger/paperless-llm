"""API request/response models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, PlainSerializer

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
from app.services.transcript import CallTiming, TranscriptItem

# All timestamps are stored in UTC; SQLite round-trips lose the offset,
# so the contract re-stamps it — every timestamp leaving the API is
# explicit UTC ("+00:00") and the frontend renders it in the user's
# chosen timezone.
UtcDateTime = Annotated[
    datetime,
    PlainSerializer(
        lambda v: (v if v.tzinfo else v.replace(tzinfo=UTC)).isoformat(),
        return_type=str,
    ),
]


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
    archived_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    proposal_count: int = 0

    model_config = {"from_attributes": True}


class SessionPage(BaseModel):
    """Pagination envelope (uniform across all app-owned lists)."""

    count: int
    page: int
    page_size: int
    results: list[SessionOut]


class ProposalPage(BaseModel):
    count: int
    page: int
    page_size: int
    results: list[ProposalOut]


class JobPage(BaseModel):
    count: int
    page: int
    page_size: int
    results: list[JobOut]


class AttemptRecord(BaseModel):
    """One entry of a step's attempt log (never shadowed by retries)."""

    model_config = {"extra": "ignore"}

    attempt: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    manual_retry_at: str | None = None


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
    attempts: list[AttemptRecord] = []
    attempt_count: int
    max_attempts: int
    scheduled_at: UtcDateTime | None
    supersedes_id: int | None
    created_at: UtcDateTime
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None
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
    created_at: UtcDateTime
    updated_at: UtcDateTime
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
    """Bulk job. The work set is deterministic: explicit document ids,
    a tag, the inbox, all untagged documents — or a set of taxonomy
    entities (entity_type + entity_ids) — never a full-text search."""

    document_ids: list[int] | None = None
    tag_id: int | None = None
    inbox: bool = False
    untagged_only: bool = False
    # Taxonomy scope: review these entities (one session per entity).
    entity_type: Literal["tag", "correspondent", "document_type"] | None = None
    entity_ids: list[int] | None = None
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
    created_at: UtcDateTime
    updated_at: UtcDateTime

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


class InstructionsUpdate(BaseModel):
    instructions: str = ""


class InstructionsOut(BaseModel):
    entity_type: str
    entity_id: int
    instructions: str


class EntityOut(BaseModel):
    """Taxonomy entity as the UI sees it: paperless fields + the
    app-local agent instructions."""

    id: int
    name: str
    match: str = ""
    matching_algorithm: int = 0
    document_count: int | None = None
    is_inbox_tag: bool = False
    color: str | None = None
    path: str | None = None
    instructions: str = ""


class DocumentOut(BaseModel):
    """Document list/detail item (content only on detail)."""

    id: int
    title: str = ""
    content: str | None = None
    tags: list[int] = []
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    created: str | None = None
    added: str | None = None
    modified: str | None = None
    archive_serial_number: int | None = None
    original_file_name: str | None = None


class DocumentSearchPage(BaseModel):
    """Proxied paperless search: ``all`` carries every matching id
    across pages (drives cross-page select-all)."""

    count: int
    page_size: int = 25
    all: list[int] | None = None
    results: list[DocumentOut]


class ResourceFetch(BaseModel):
    in_flight: int = 0
    last_fetched_at: UtcDateTime | None = None
    last_error: str | None = None


class SyncStatusOut(BaseModel):
    resources: dict[str, ResourceFetch]


class MetaOut(BaseModel):
    version: str = ""
    paperless_url: str


class AuthMeOut(BaseModel):
    mode: Literal["none", "proxy", "paperless"]
    user: str | None = None


class HealthOut(BaseModel):
    status: str


class RevertCheckOut(BaseModel):
    revert_noop: bool


class AuditEntryOut(BaseModel):
    id: int
    ts: UtcDateTime
    kind: str
    action: str
    actor: str = "system"
    detail: dict[str, Any] = {}


class AuditPage(BaseModel):
    count: int
    page: int
    page_size: int
    results: list[AuditEntryOut]


class EntityRefOut(BaseModel):
    id: int
    name: str
    document_count: int | None = None


class MergeCandidateOut(BaseModel):
    entity_type: str
    source: EntityRefOut
    target: EntityRefOut
    string_score: float
    semantic_score: float | None


class OcrReviewOut(BaseModel):
    """Data for the OCR gate diff view. Deliberately excludes internal
    scoring (similarity) — the user reviews text, not metrics."""

    document_id: int
    previous_content: str
    ocr_text: str
    pages: int
    # Per-batch LLM call metrics of the OCR run (+ page range label).
    timings: list[OcrBatchTiming] = []


class OcrBatchTiming(CallTiming):
    pages: str | None = None


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
