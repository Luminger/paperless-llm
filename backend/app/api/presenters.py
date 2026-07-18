"""Serializers shared across routers — routes import from here, never
from each other."""

from __future__ import annotations

from app.api.schemas import ProposalOut
from app.db.models import Proposal


def proposal_out(p: Proposal) -> ProposalOut:
    out = ProposalOut.model_validate(p)
    if p.applied_change:
        out.applied = True
        out.reverted = p.applied_change.reverted_at is not None
    return out
