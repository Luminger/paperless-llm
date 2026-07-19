"""API request/response models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, PlainSerializer

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
    # Run name ("Analysis", "OCR pass") — user-renamable.
    title: str
    # Entity name resolved LIVE at read time (snapshots go stale).
    entity_name: str = ""
    status: SessionStatus
    phase: SessionPhase | None
    params: dict[str, Any] = {}
    error: str | None
    job_id: int | None = None
    archived_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    proposal_count: int = 0
    # Proposals actually WAITING on the user — the attention signal.
    pending_proposal_count: int = 0
    # Proposals applied to paperless (excluding the internal OCR write).
    applied_proposal_count: int = 0

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
    # The turn that emitted it (live rendering matches on this).
    step_id: int | None = None
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
    # Who applied it: "user", "user:<name>", or "system" (auto-apply).
    applied_by: str | None = None
    applied_at: UtcDateTime | None = None

    model_config = {"from_attributes": True}


class ProposalPatch(BaseModel):
    user_payload: dict[str, Any] | None = None


class SessionRename(BaseModel):
    title: str


class AnalyzeRequest(BaseModel):
    redo_ocr: bool = False
    # Re-OCR and STOP: no analysis follows (the document page's
    # dedicated OCR action).
    ocr_only: bool = False
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
    all_documents: bool = False
    # Taxonomy scope: review these entities (one session per entity).
    entity_type: Literal["tag", "correspondent", "document_type"] | None = None
    entity_ids: list[int] | None = None
    redo_ocr: bool = False
    # Re-OCR each document and STOP there — no analysis follows.
    ocr_only: bool = False
    # Corpus curation: analyze the next N never-analyzed documents
    # (oldest first). Mutually exclusive with the other scopes.
    next_batch: int | None = Field(default=None, ge=1, le=100)
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


class JobAttentionOut(BaseModel):
    """Flow-through review: the next session in a job waiting on the
    user, plus how many still are (including the current one)."""

    next_session_id: int | None = None
    remaining: int = 0


class CorpusOut(BaseModel):
    """Corpus-curation progress: how much of the archive ever went
    through a completed analysis."""

    total: int
    processed: int


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


class CustomFieldValueOut(BaseModel):
    """One custom-field value on a document."""

    field: int
    value: Any = None


class CustomFieldOut(BaseModel):
    """A paperless custom-field definition, as the UI needs it: the name
    behind the id, the value type for the editor widget, and select
    options when the type is `select`."""

    id: int
    name: str
    data_type: str
    select_options: list[dict[str, Any]] = []


class EntityOut(BaseModel):
    """Taxonomy entity as the UI sees it: paperless fields + the
    app-local agent instructions."""

    id: int
    name: str
    match: str = ""
    matching_algorithm: int = 0
    is_insensitive: bool = True
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
    # Names/types resolve via /api/entities/custom_fields.
    custom_fields: list[CustomFieldValueOut] = []


class DocumentHistoryOut(BaseModel):
    """One applied change on a document: what, when, by whom, and the
    session it came from."""

    proposal_id: int
    session_id: int | None = None
    session_title: str = ""
    kind: str
    # Which fields the applied payload touched (identity keys stripped).
    fields: list[str] = []
    applied_at: UtcDateTime
    # "user:<name>" or "system" (auto-apply).
    applied_by: str
    edited: bool = False
    reverted: bool = False


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
    user: str | None = None
    role: Literal["admin", "user"] = "user"


class AuthSessionOut(BaseModel):
    """A live login session (Settings → Sessions). `sid` is an opaque
    revocation handle, not an enumeration surface."""

    sid: str
    username: str
    role: str = "user"
    user_agent: str = ""
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool = False


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
