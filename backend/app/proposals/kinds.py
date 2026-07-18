"""Shared proposal-kind knowledge (AUDIT centralization: the
`kind != "replace_content"` string test appeared at ~8 call sites — the
day a second internal kind exists, every copy is a bug site).
"""

from __future__ import annotations

from typing import Any

from app.db.models import Proposal

# Kinds that exist for internal plumbing (the OCR gate's journaled
# content write). They never appear in user-facing proposal lists,
# counts, or the decision loop.
INTERNAL_KINDS: tuple[str, ...] = ("replace_content",)


def is_internal(kind: Any) -> bool:
    return str(kind) in INTERNAL_KINDS


def visible():  # SQLAlchemy criterion
    return Proposal.kind.notin_(INTERNAL_KINDS)


# Entity rule fields — create/update payloads, revert field selection,
# merge/delete recreation. ONE list (apply.py used to carry four copies).
ENTITY_RULE_FIELDS: tuple[str, ...] = (
    "name",
    "match",
    "matching_algorithm",
    "is_insensitive",
)

# paperless matching_algorithm value for auto (ML) matching — the
# default for agent-created entities so the classifier keeps learning.
MATCHING_AUTO = 6
