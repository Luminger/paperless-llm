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
        doc_ids = wanted.pop("document", set())
        if doc_ids:
            page = await paperless.search_documents(
                document_ids=sorted(doc_ids), page_size=100
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
