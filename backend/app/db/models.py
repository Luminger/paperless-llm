"""SQLAlchemy models. See DESIGN.md "Sessions, proposals, steering".

Portable across SQLite and PostgreSQL; JSON columns use JSONB on PG.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

PortableJSON = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: PortableJSON, list[Any]: PortableJSON}


class AgentKind(enum.StrEnum):
    document = "document"
    tag = "tag"
    correspondent = "correspondent"
    document_type = "document_type"


class EntityType(enum.StrEnum):
    document = "document"
    tag = "tag"
    correspondent = "correspondent"
    document_type = "document_type"
    storage_path = "storage_path"


class SessionStatus(enum.StrEnum):
    idle = "idle"  # no agent run in flight; steerable
    running = "running"  # an agent run is executing
    failed = "failed"  # last run errored; still steerable
    archived = "archived"


class SessionPhase(enum.StrEnum):
    """Document-analysis pipeline stage. The OCR review is a GATE: the
    pipeline stops there until the user accepts/fixes/skips the OCR
    result; only then does the metadata analysis run — on the post-gate
    content."""

    queued = "queued"
    ocr_running = "ocr_running"
    ocr_review = "ocr_review"  # gate: waiting for the user
    analyzing = "analyzing"
    done = "done"


class ProposalStatus(enum.StrEnum):
    draft = "draft"  # emitted mid-run; run not finished yet
    pending = "pending"  # awaiting review
    approved = "approved"  # approved, not yet applied
    rejected = "rejected"
    applied = "applied"
    superseded = "superseded"  # replaced by a newer revision


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class QueueLane(enum.StrEnum):
    """Two lanes so chat turns never wait behind bulk work."""

    interactive = "interactive"
    batch = "batch"


class QueueState(enum.StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class Session(Base):
    """One conversation with one agent, optionally bound to an entity."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_kind: Mapped[AgentKind] = mapped_column(Enum(AgentKind, native_enum=False))
    entity_type: Mapped[EntityType | None] = mapped_column(
        Enum(EntityType, native_enum=False), nullable=True
    )
    entity_id: Mapped[int | None] = mapped_column(nullable=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, native_enum=False), default=SessionStatus.idle
    )
    # Pipeline stage; NULL for sessions without a pipeline (steering-only).
    phase: Mapped[SessionPhase | None] = mapped_column(
        Enum(SessionPhase, native_enum=False), nullable=True
    )
    # Analysis parameters (redo_ocr, instructions, ...).
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Serialized pydantic-ai message history (ModelMessagesTypeAdapter).
    message_history: Mapped[list[Any]] = mapped_column(default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    proposals: Mapped[list[Proposal]] = relationship(back_populates="session")

    __table_args__ = (Index("ix_sessions_entity", "entity_type", "entity_id"),)


class Proposal(Base):
    """A typed, reviewable change emitted by an agent.

    ``agent_payload`` is immutable — exactly what the model emitted.
    ``user_payload`` is the user's edited copy (full edit visibility).
    Revisions supersede one another via ``supersedes_id``; the chain is
    preserved and visible.
    """

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"))
    kind: Mapped[str] = mapped_column(String(50))  # ProposalKind discriminator
    revision: Mapped[int] = mapped_column(default=1)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("proposals.id"), nullable=True)
    agent_payload: Mapped[dict[str, Any]] = mapped_column()
    user_payload: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    status: Mapped[ProposalStatus] = mapped_column(
        Enum(ProposalStatus, native_enum=False), default=ProposalStatus.draft
    )
    # Denormalized for queue filtering without joining sessions.
    entity_type: Mapped[EntityType | None] = mapped_column(
        Enum(EntityType, native_enum=False), nullable=True
    )
    entity_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    session: Mapped[Session] = relationship(back_populates="proposals")
    applied_change: Mapped[AppliedChange | None] = relationship(back_populates="proposal")

    __table_args__ = (
        Index("ix_proposals_status", "status"),
        Index("ix_proposals_entity", "entity_type", "entity_id"),
    )


class AppliedChange(Base):
    """Undo journal: before/after snapshots of every applied proposal."""

    __tablename__ = "applied_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"))
    # Snapshot of the paperless state we touched, sufficient to restore.
    paperless_before: Mapped[dict[str, Any]] = mapped_column()
    paperless_after: Mapped[dict[str, Any]] = mapped_column()
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    proposal: Mapped[Proposal] = relationship(back_populates="applied_change")


class Job(Base):
    """A batch of work (bulk analysis, webhook ingest, reindex, ...)."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))
    # apply_policy: "review" | "auto", batch filters, etc.
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False), default=JobStatus.queued
    )
    total: Mapped[int] = mapped_column(default=0)
    done: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class QueueItem(Base):
    """Persistent work queue: one row per pipeline-stage invocation.
    In-process async workers claim rows; queued work survives restarts
    (running items are retried on startup). Single-node by design — see
    DESIGN.md "Queueing"."""

    __tablename__ = "queue_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    lane: Mapped[QueueLane] = mapped_column(
        Enum(QueueLane, native_enum=False), default=QueueLane.batch
    )
    # Stage name resolved via the queue dispatch table.
    stage: Mapped[str] = mapped_column(String(50))
    args: Mapped[dict[str, Any]] = mapped_column(default=dict)
    state: Mapped[QueueState] = mapped_column(
        Enum(QueueState, native_enum=False), default=QueueState.pending
    )
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id"), nullable=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_queue_claim", "state", "lane", "id"),)


class EntityEmbedding(Base):
    """Cached name embeddings for taxonomy entities (entity index)."""

    __tablename__ = "entity_embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int] = mapped_column()
    name: Mapped[str] = mapped_column(String(500))
    vector: Mapped[list[Any]] = mapped_column(default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_entity_embeddings_key", "entity_type", "entity_id", unique=True),
    )


class OcrResult(Base):
    """Cache of OCR pipeline runs, keyed by content + model + prompt."""

    __tablename__ = "ocr_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column()
    checksum: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[int] = mapped_column()
    pages: Mapped[list[Any]] = mapped_column(default=list)  # per-page markdown
    text: Mapped[str] = mapped_column(Text, default="")
    # Similarity vs. the paperless `content` at OCR time (0..1).
    similarity: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index(
            "ix_ocr_key", "document_id", "checksum", "model", "prompt_version", unique=True
        ),
    )
