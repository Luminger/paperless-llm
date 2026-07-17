"""API request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.db.models import (
    AgentKind,
    EntityType,
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


class SessionDetailOut(SessionOut):
    transcript: list[TranscriptItem] = []
    proposals: list[ProposalOut] = []


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


class OcrReviewOut(BaseModel):
    """Data for the OCR gate diff view. Deliberately excludes internal
    scoring (similarity) — the user reviews text, not metrics."""

    document_id: int
    previous_content: str
    ocr_text: str
    pages: int


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
