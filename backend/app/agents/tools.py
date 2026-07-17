"""Shared toolset. Registered on every agent; taxonomy-specific agents
get the full set too — scope is controlled by the system prompt, safety
by the proposal/review layer (agents cannot write to paperless at all).

Read tools return compact, LLM-friendly summaries. ``propose_*`` tools
are the ONLY way agents effect change: they persist draft Proposal rows
reviewed by a human (or auto-applied per job policy).
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import ModelRetry, RunContext

from app.agents.deps import AgentDeps, clamp_text
from app.db.models import EntityType, Proposal, ProposalStatus
from app.llm.ocr import run_ocr
from app.paperless import PaperlessError
from app.proposals.schemas import (
    AnyProposal,
    CreateEntity,
    DeleteEntity,
    MergeEntities,
    TaxonomyType,
    UpdateDocumentMetadata,
    UpdateEntity,
    dump_payload,
)

IntList = list[int] | str | int | None


def _int_list(v: IntList) -> list[int]:
    """Tolerant list-of-ints coercion.

    Some serving-stack tool parsers (e.g. vLLM's qwen3_xml) mangle JSON
    array arguments; models then fall back to "1,2,3" or "[1, 2]"
    strings or bare ints. Accept all of it — observed live with
    Qwen3.6-27b, which otherwise gives up on list-typed args.
    """
    if v is None:
        return []
    if isinstance(v, int):
        return [v]
    if isinstance(v, str):
        cleaned = v.strip().strip("[]")
        return [int(part) for part in cleaned.replace(";", ",").split(",") if part.strip()]
    return [int(x) for x in v]


MATCHING_ALGORITHMS = {
    0: "none",
    1: "any word",
    2: "all words",
    3: "exact match",
    4: "regex",
    5: "fuzzy",
    6: "auto (ML)",
}


def _doc_summary(d: Any) -> dict[str, Any]:
    return {
        "id": d.id,
        "title": d.title,
        "correspondent_id": d.correspondent,
        "document_type_id": d.document_type,
        "storage_path_id": d.storage_path,
        "tag_ids": d.tags,
        "created": d.created,
        "asn": d.archive_serial_number,
    }


def _entity_summary(e: Any) -> dict[str, Any]:
    return {
        "id": e.id,
        "name": e.name,
        "document_count": e.document_count,
        "match": e.match,
        "matching_algorithm": MATCHING_ALGORITHMS.get(e.matching_algorithm, "?"),
    }


# ----- read tools -----------------------------------------------------


async def search_documents(
    ctx: RunContext[AgentDeps],
    query: str | None = None,
    title_contains: str | None = None,
    tag_ids: IntList = None,
    untagged_only: bool = False,
    correspondent_id: int | None = None,
    without_correspondent: bool = False,
    document_type_id: int | None = None,
    without_document_type: bool = False,
    created_after: str | None = None,
    created_before: str | None = None,
    page: int = 1,
) -> dict[str, Any]:
    """Search documents. `query` is full-text search over OCR content and
    metadata (supports quoted phrases, AND/OR, field:value). All other
    arguments are exact field filters and can be combined with `query`.
    Dates are ISO (YYYY-MM-DD). Returns up to 25 results per page plus
    the total count."""
    result = await ctx.deps.paperless.search_documents(
        query=query,
        title_contains=title_contains,
        tag_ids=_int_list(tag_ids) or None,
        tags_none=True if untagged_only else None,
        correspondent_none=True if without_correspondent else None,
        correspondent_id=correspondent_id,
        document_type_id=document_type_id,
        document_type_none=True if without_document_type else None,
        created_after=created_after,
        created_before=created_before,
        page=page,
    )
    return {"total": result.count, "page": page, "documents": [
        _doc_summary(d) for d in result.results
    ]}


async def get_document(ctx: RunContext[AgentDeps], document_id: int) -> dict[str, Any]:
    """Fetch one document: full metadata and the beginning of its OCR
    content. Use get_document_content for more of the content."""
    d = await ctx.deps.paperless.get_document(document_id)
    return _doc_summary(d) | {
        "original_file_name": d.original_file_name,
        "added": d.added,
        "custom_fields": [cf.model_dump() for cf in d.custom_fields],
        "content_preview": clamp_text(d.content, 2000),
        "content_length": len(d.content),
    }


async def get_document_content(
    ctx: RunContext[AgentDeps], document_id: int, offset: int = 0
) -> str:
    """Read the stored OCR content of a document, starting at character
    `offset`. Long content is truncated; call again with a higher offset
    to continue reading."""
    d = await ctx.deps.paperless.get_document(document_id)
    return clamp_text(d.content[offset:], ctx.deps.max_chars // 4, note=f", offset={offset}")


async def list_tags(ctx: RunContext[AgentDeps]) -> list[dict[str, Any]]:
    """List all tags with document counts and matching rules."""
    return [_entity_summary(t) for t in await ctx.deps.paperless.list_tags()]


async def list_correspondents(ctx: RunContext[AgentDeps]) -> list[dict[str, Any]]:
    """List all correspondents with document counts and matching rules."""
    return [_entity_summary(c) for c in await ctx.deps.paperless.list_correspondents()]


async def list_document_types(ctx: RunContext[AgentDeps]) -> list[dict[str, Any]]:
    """List all document types with document counts and matching rules."""
    return [_entity_summary(dt) for dt in await ctx.deps.paperless.list_document_types()]


async def ocr_document(
    ctx: RunContext[AgentDeps], document_id: int, force: bool = False
) -> dict[str, Any]:
    """Re-OCR a document with the local vision model (page by page) and
    compare the result against the OCR text paperless already has.
    Returns the new text (truncated if long) and a similarity score
    0..1 — low similarity means the existing OCR is likely bad. Results
    are cached; `force=True` re-runs anyway."""
    outcome = await run_ocr(ctx.deps.paperless, ctx.deps.db, document_id, force=force)
    return {
        "document_id": outcome.document_id,
        "pages": len(outcome.pages),
        "similarity_to_existing": outcome.similarity,
        "from_cache": outcome.from_cache,
        "text": clamp_text(outcome.text, ctx.deps.max_chars // 4),
        "text_length": len(outcome.text),
    }


# ----- propose tools --------------------------------------------------
#
# Harness-level guarantees (beyond type validation):
#  - referenced entity/document ids must exist (else ModelRetry),
#  - proposals must actually CHANGE something: no-op fields are
#    silently stripped, an entirely no-op proposal is rejected back to
#    the model. "Nothing to propose" is expressed by not proposing.


async def _require_entity(ctx: RunContext[AgentDeps], entity_type: str, entity_id: int):
    for e in await ctx.deps.taxonomy(entity_type):
        if e.id == entity_id:
            return e
    raise ModelRetry(
        f"No {entity_type} with id={entity_id} exists. Use the list/search "
        "tools to find the correct id; never guess ids."
    )


async def _require_document(ctx: RunContext[AgentDeps], document_id: int):
    try:
        return await ctx.deps.paperless.get_document(document_id)
    except PaperlessError as e:
        if e.status_code == 404:
            raise ModelRetry(f"Document id={document_id} does not exist.") from e
        raise


async def _persist(ctx: RunContext[AgentDeps], p: AnyProposal,
                   entity_type: EntityType | None, entity_id: int | None) -> str:
    proposal = Proposal(
        session_id=ctx.deps.session_id,
        kind=str(p.kind),
        agent_payload=dump_payload(p),
        status=ProposalStatus.draft,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    ctx.deps.db.add(proposal)
    await ctx.deps.db.flush()
    ctx.deps.emitted.append(proposal)
    return (
        f"Proposal #{proposal.id} ({p.kind}) recorded for human review. "
        "It is NOT applied yet. Do not repeat it; continue with your task "
        "or finish with a short summary."
    )


async def propose_update_document_metadata(
    ctx: RunContext[AgentDeps],
    document_id: int,
    reason: str,
    title: str | None = None,
    correspondent: int | None = None,
    document_type: int | None = None,
    storage_path: int | None = None,
    created: str | None = None,
    add_tags: IntList = None,
    remove_tags: IntList = None,
) -> str:
    """Propose metadata changes for one document. Provide ONLY the fields
    you want to change — values identical to the document's current state
    are rejected as no-ops. IDs must be existing entity ids (verify via
    the list/search tools first; propose_create_entity for genuinely new
    ones). `created` is the document's creation date (ISO), usually the
    date printed on the document. Tag id lists may be given as JSON
    arrays or comma-separated strings ("1,2"). Always give a concise
    `reason`."""
    doc = await _require_document(ctx, document_id)

    # Referential checks.
    if correspondent is not None:
        await _require_entity(ctx, "correspondent", correspondent)
    if document_type is not None:
        await _require_entity(ctx, "document_type", document_type)
    if storage_path is not None:
        await _require_entity(ctx, "storage_path", storage_path)
    for t in _int_list(add_tags):
        await _require_entity(ctx, "tag", t)

    # Strip no-op fields.
    dropped: list[str] = []
    fields: dict[str, Any] = {"document_id": document_id, "reason": reason}
    scalars = {
        "title": (title, doc.title),
        "correspondent": (correspondent, doc.correspondent),
        "document_type": (document_type, doc.document_type),
        "storage_path": (storage_path, doc.storage_path),
        "created": (
            created[:10] if created else None,
            (doc.created or "")[:10] or None,
        ),
    }
    for k, (proposed, current) in scalars.items():
        if proposed is None:
            continue
        if proposed == current:
            dropped.append(k)
        else:
            fields[k] = proposed
    add = [t for t in _int_list(add_tags) if t not in doc.tags]
    remove = [t for t in _int_list(remove_tags) if t in doc.tags]
    if set(_int_list(add_tags)) - set(add) or set(_int_list(remove_tags)) - set(remove):
        dropped.append("tags(partially)")
    if add:
        fields["add_tags"] = add
    if remove:
        fields["remove_tags"] = remove

    if set(fields) == {"document_id", "reason"}:
        raise ModelRetry(
            "Proposal rejected: every proposed value matches the document's "
            "current state. Do not propose no-op changes — if the current "
            "data is already correct, simply finish without a proposal."
        )

    p = UpdateDocumentMetadata.model_validate(fields)
    note = f" (no-op fields dropped: {', '.join(dropped)})" if dropped else ""
    return await _persist(ctx, p, EntityType.document, document_id) + note


async def propose_create_entity(
    ctx: RunContext[AgentDeps],
    entity_type: TaxonomyType,
    name: str,
    reason: str,
    match: str | None = None,
    matching_algorithm: int | None = None,
    assign_to_documents: IntList = None,
) -> str:
    """Propose creating a new tag/correspondent/document_type. FIRST check
    the existing entities (list tools) — never create near-duplicates of
    existing entries. matching_algorithm: 1=any word, 2=all words,
    3=exact, 4=regex, 6=auto. Optionally assign the new entity to
    documents immediately (JSON array or comma-separated string)."""
    for e in await ctx.deps.taxonomy(entity_type):
        if e.name.strip().lower() == name.strip().lower():
            raise ModelRetry(
                f"Proposal rejected: a {entity_type} named {e.name!r} already "
                f"exists (id={e.id}). Assign the existing entity instead of "
                "creating a duplicate."
            )
    p = CreateEntity(
        entity_type=entity_type,
        name=name,
        reason=reason,
        match=match,
        matching_algorithm=matching_algorithm,
        assign_to_documents=_int_list(assign_to_documents),
    )
    return await _persist(ctx, p, EntityType(entity_type), None)


async def propose_update_entity(
    ctx: RunContext[AgentDeps],
    entity_type: TaxonomyType,
    entity_id: int,
    reason: str,
    name: str | None = None,
    match: str | None = None,
    matching_algorithm: int | None = None,
) -> str:
    """Propose renaming an entity or fixing its matching rule. Provide
    only the fields to change — values identical to the entity's current
    state are rejected as no-ops."""
    entity = await _require_entity(ctx, entity_type, entity_id)
    changes = {
        k: v
        for k, v in (("name", name), ("match", match), ("matching_algorithm", matching_algorithm))
        if v is not None and v != getattr(entity, k)
    }
    if not changes:
        raise ModelRetry(
            "Proposal rejected: the proposed values match the entity's "
            "current state. If nothing needs to change, finish without a "
            "proposal."
        )
    name, match, matching_algorithm = (
        changes.get("name"),
        changes.get("match"),
        changes.get("matching_algorithm"),
    )
    p = UpdateEntity(
        entity_type=entity_type,
        entity_id=entity_id,
        reason=reason,
        name=name,
        match=match,
        matching_algorithm=matching_algorithm,
    )
    return await _persist(ctx, p, EntityType(entity_type), entity_id)


async def propose_merge_entities(
    ctx: RunContext[AgentDeps],
    entity_type: TaxonomyType,
    source_id: int,
    target_id: int,
    reason: str,
) -> str:
    """Propose merging entity `source_id` INTO `target_id`: all documents
    are reassigned to the target, then the source is deleted. The target
    (usually the better-named / larger one) survives."""
    if source_id == target_id:
        raise ModelRetry("Proposal rejected: source and target are the same entity.")
    await _require_entity(ctx, entity_type, source_id)
    await _require_entity(ctx, entity_type, target_id)
    p = MergeEntities(
        entity_type=entity_type, source_id=source_id, target_id=target_id, reason=reason
    )
    return await _persist(ctx, p, EntityType(entity_type), source_id)


async def propose_delete_entity(
    ctx: RunContext[AgentDeps],
    entity_type: TaxonomyType,
    entity_id: int,
    reason: str,
    force: bool = False,
) -> str:
    """Propose deleting an entity. Only for genuinely useless entries
    (empty, or nonsense). Use propose_merge_entities when documents
    should keep an equivalent label. force=True detaches documents first."""
    await _require_entity(ctx, entity_type, entity_id)
    p = DeleteEntity(entity_type=entity_type, entity_id=entity_id, reason=reason, force=force)
    return await _persist(ctx, p, EntityType(entity_type), entity_id)


READ_TOOLS = [
    search_documents,
    get_document,
    get_document_content,
    list_tags,
    list_correspondents,
    list_document_types,
    ocr_document,
]

PROPOSE_TOOLS = [
    propose_update_document_metadata,
    propose_create_entity,
    propose_update_entity,
    propose_merge_entities,
    propose_delete_entity,
]

ALL_TOOLS = READ_TOOLS + PROPOSE_TOOLS

# The DocumentAgent works AFTER the user-gated OCR step: it neither
# re-OCRs nor rewrites content, and its proposal scope is the document
# itself (metadata + genuinely missing entities).
DOCUMENT_AGENT_TOOLS = [
    t
    for t in ALL_TOOLS
    if t
    not in (ocr_document, propose_update_entity, propose_merge_entities, propose_delete_entity)
]
