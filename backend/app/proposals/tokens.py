"""THE proposal-token contract.

The agent's tool results reference proposals as ``[[proposal:ID]]``;
the transcript builder and the live SSE wrapper parse them back. Format
and regex live HERE and nowhere else (they used to be split across
tools.py / transcript.py / registry.py).
"""

from __future__ import annotations

import re

PROPOSAL_TOKEN_RE = re.compile(r"\[\[proposal:(\d+)\]\]")


def proposal_token(proposal_id: int) -> str:
    return f"[[proposal:{proposal_id}]]"


def find_proposal_id(text: str) -> int | None:
    m = PROPOSAL_TOKEN_RE.search(text)
    return int(m.group(1)) if m else None
