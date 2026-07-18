"""Live entity-name resolution for list rows.

Session titles describe the RUN ("Analysis", "OCR pass") — the entity
name is resolved fresh at read time, because the analysis itself
renames documents and a snapshot would lie within minutes. One batched
paperless call per entity type; failures degrade to empty names (the
UI falls back to the run title).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from app.db.models import EntityType
from app.paperless import PaperlessClient
from app.paperless.taxonomy import TAXONOMY

log = logging.getLogger(__name__)

Key = tuple[str, int]


async def entity_names(
    paperless: PaperlessClient,
    items: Iterable[tuple[EntityType | None, int | None]],
) -> dict[Key, str]:
    wanted: dict[str, set[int]] = {}
    for etype, eid in items:
        if etype is None or eid is None:
            continue
        wanted.setdefault(etype.value, set()).add(eid)
    out: dict[Key, str] = {}
    try:
        doc_ids = sorted(wanted.pop("document", set()))
        # AUDIT API-F15: id__in chunks of 100 — a page bound to >100
        # distinct documents must not silently lose names.
        for i in range(0, len(doc_ids), 100):
            page = await paperless.search_documents(
                document_ids=doc_ids[i : i + 100], page_size=100
            )
            for d in page.results:
                out[("document", d.id)] = d.title
        for type_name, ids in wanted.items():
            spec = TAXONOMY.get(type_name)
            if spec is None:
                continue
            for e in await spec.list(paperless):
                if e.id in ids:
                    out[(type_name, e.id)] = e.name
    except Exception:  # noqa: BLE001 — names are cosmetic, never fatal
        log.debug("entity name enrichment failed", exc_info=True)
    return out


async def proposal_counts(db, session_ids: list[int]) -> dict[int, tuple[int, int, int]]:
    """(total, pending, applied) VISIBLE proposal counts per session —
    the one implementation of the badge numbers (was duplicated verbatim
    in sessions.py and jobs.py)."""
    from sqlalchemy import case, func, select

    from app.db.models import Proposal, ProposalStatus
    from app.proposals.kinds import visible

    if not session_ids:
        return {}
    pending_case = case(
        ((Proposal.status == ProposalStatus.pending) & visible(), 1), else_=0
    )
    applied_case = case(
        ((Proposal.status == ProposalStatus.applied) & visible(), 1), else_=0
    )
    return {
        sid: (n, int(pending or 0), int(applied or 0))
        for sid, n, pending, applied in (
            await db.execute(
                select(
                    Proposal.session_id,
                    func.count(),
                    func.sum(pending_case),
                    func.sum(applied_case),
                )
                .where(Proposal.session_id.in_(session_ids))
                .group_by(Proposal.session_id)
            )
        ).all()
    }


async def apply_entity_names(paperless, items) -> None:
    """Back-fill ``entity_name`` on SessionOut-shaped objects (live
    resolution — snapshots go stale). One loop, not three copies."""
    names = await entity_names(
        paperless, [(i.entity_type, i.entity_id) for i in items]
    )
    for i in items:
        if i.entity_type is not None and i.entity_id is not None:
            i.entity_name = names.get((i.entity_type.value, i.entity_id), "")
