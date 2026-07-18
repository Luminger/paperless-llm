"""Shared toolset. Registered on every agent; taxonomy-specific agents
get the full set too — scope is controlled by the system prompt, safety
by the proposal/review layer (agents cannot write to paperless at all).

Read tools return compact, LLM-friendly summaries. ``propose_*`` tools
are the ONLY way agents effect change: they persist draft Proposal rows
reviewed by a human (or auto-applied per job policy).
"""

from __future__ import annotations

import logging
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

log = logging.getLogger(__name__)

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

# Algorithms that require a match pattern (word/regex/fuzzy matching).
_PATTERN_ALGOS = {1, 2, 3, 4, 5}


def _check_matching(match: str | None, matching_algorithm: int | None) -> None:
    """Guard the paperless matching-rule combos before proposing."""
    if matching_algorithm is not None and matching_algorithm not in MATCHING_ALGORITHMS:
        raise ModelRetry(
            f"Proposal rejected: unknown matching_algorithm {matching_algorithm}. "
            "Valid: 0=none, 1=any word, 2=all words, 3=exact, 4=regex, "
            "5=fuzzy, 6=auto (ML)."
        )
    if matching_algorithm in _PATTERN_ALGOS and not match:
        raise ModelRetry(
            f"Proposal rejected: matching_algorithm "
            f"{MATCHING_ALGORITHMS[matching_algorithm]!r} requires a `match` "
            "pattern. Provide one, or use 6 (auto) which learns from the "
            "user's decisions and needs no pattern."
        )
    if match and matching_algorithm in (0, 6):
        raise ModelRetry(
            "Proposal rejected: a `match` pattern only makes sense with "
            "algorithms 1-5; auto (6) and none (0) must not have one."
        )
    if match and matching_algorithm is None:
        raise ModelRetry(
            "Proposal rejected: a `match` pattern needs an explicit "
            "matching_algorithm (1=any word, 2=all words, 3=exact, 4=regex, "
            "5=fuzzy)."
        )


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
    """Search documents by exact criteria. `query` is full-text search
    over OCR content and metadata (supports quoted phrases, AND/OR,
    field:value); all other arguments are exact field filters and can
    be combined with `query`. Dates are ISO (YYYY-MM-DD). Returns up to
    25 results per page plus the total count. For "which document is
    about X" relevance lookups prefer find_documents."""
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


async def find_documents(
    ctx: RunContext[AgentDeps], query: str, top_k: int = 8
) -> dict[str, Any]:
    """Find the documents most RELEVANT to a natural-language query, in
    an archive of any size. Two stages: full-text recall across all
    documents, then a semantic relevance rerank (when configured).
    Returns at most top_k compact summaries with a short snippet each —
    read a hit with get_document / get_document_content. Use this for
    "the document about X"; use search_documents for exact field
    filters and structured queries."""
    from app.llm.rerank import rerank, rerank_enabled

    top_k = max(1, min(int(top_k), 20))
    page = await ctx.deps.paperless.search_documents(query=query, page_size=50)
    docs = list(page.results)
    order = list(range(len(docs)))
    reranked = False
    if docs and rerank_enabled():
        # Title + head of content is what a reranker can actually use;
        # full documents would drown it, and CPU rerankers are
        # latency-bound on text length (~1000 chars ≈ 6s for 50 docs
        # on a Ryzen 9700X — measured, not guessed).
        texts = [f"{d.title}\n{(d.content or '')[:1000]}" for d in docs]
        try:
            order = await rerank(query, texts, top_n=top_k)
            reranked = True
        except Exception:  # noqa: BLE001 — ranked order is a bonus, never fatal
            log.warning("rerank failed; keeping full-text order", exc_info=True)
    picked = [docs[i] for i in order[:top_k]]
    return {
        "total_matches": page.count,
        "reranked": reranked,
        "documents": [
            _doc_summary(d) | {"snippet": clamp_text(d.content or "", 240)}
            for d in picked
        ],
    }


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


async def _summaries_with_instructions(
    ctx: RunContext[AgentDeps], entity_type: str, entities: list[Any]
) -> list[dict[str, Any]]:
    """Entity summaries + the user's app-local instructions (which the
    agent MUST obey when using the entity)."""
    from app.services.instructions import ensure_inbox_defaults, get_map

    if entity_type == "tag":
        await ensure_inbox_defaults(ctx.deps.db, entities)
    instr = await get_map(ctx.deps.db, entity_type)
    out = []
    for e in entities:
        summary = _entity_summary(e)
        if e.id in instr:
            summary["user_instructions"] = instr[e.id]
        out.append(summary)
    return out


async def list_tags(ctx: RunContext[AgentDeps]) -> list[dict[str, Any]]:
    """List all tags with document counts, matching rules, and any
    user_instructions attached to them (these are binding)."""
    return await _summaries_with_instructions(
        ctx, "tag", await ctx.deps.paperless.list_tags()
    )


async def list_correspondents(ctx: RunContext[AgentDeps]) -> list[dict[str, Any]]:
    """List all correspondents with document counts, matching rules, and
    any user_instructions attached to them (these are binding)."""
    return await _summaries_with_instructions(
        ctx, "correspondent", await ctx.deps.paperless.list_correspondents()
    )


async def list_document_types(ctx: RunContext[AgentDeps]) -> list[dict[str, Any]]:
    """List all document types with document counts, matching rules, and
    any user_instructions attached to them (these are binding)."""
    return await _summaries_with_instructions(
        ctx, "document_type", await ctx.deps.paperless.list_document_types()
    )


async def find_similar_entities(
    ctx: RunContext[AgentDeps],
    entity_type: TaxonomyType,
    name: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Find existing entities whose names are similar to `name` (string
    distance plus semantic similarity when available). Scores are 0..1;
    anything above ~0.85 is likely the same thing. ALWAYS check this
    before creating a new entity, and use it to hunt duplicates when
    reviewing the taxonomy."""
    from app.services.entity_index import find_similar

    results = await find_similar(ctx.deps.db, ctx.deps.paperless, entity_type, name, top_k)
    return [
        {k: v for k, v in r.items() if k in ("id", "name", "document_count", "similarity")}
        for r in results
    ]


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
                   entity_type: EntityType | None, entity_id: int | None,
                   snapshot: dict[str, Any] | None = None) -> str:
    if ctx.deps.emitted:
        raise ModelRetry(
            "One proposal per turn: you already emitted a proposal in this "
            "turn. Stop proposing and finish with your summary now — after "
            "the user decides on it you will get another turn for the next "
            "change."
        )
    proposal = Proposal(
        session_id=ctx.deps.session_id,
        step_id=ctx.deps.step_id,
        kind=str(p.kind),
        agent_payload=dump_payload(p),
        base_snapshot=snapshot,
        status=ProposalStatus.draft,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    ctx.deps.db.add(proposal)
    await ctx.deps.db.flush()
    ctx.deps.emitted.append(proposal)
    return (
        f"Proposal [[proposal:{proposal.id}]] ({p.kind}) recorded for human "
        "review. It is NOT applied yet. Do not repeat it; continue with "
        "your task or finish with a short summary."
    )


async def propose_update_document_metadata(
    ctx: RunContext[AgentDeps],
    document_id: int,
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
    arrays or comma-separated strings ("1,2"). Explain your changes in
    your final summary, not in the proposal."""
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
    fields: dict[str, Any] = {"document_id": document_id}
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

    if set(fields) == {"document_id"}:
        raise ModelRetry(
            "Proposal rejected: every proposed value matches the document's "
            "current state. Do not propose no-op changes — if the current "
            "data is already correct, simply finish without a proposal."
        )

    p = UpdateDocumentMetadata.model_validate(fields)
    note = f" (no-op fields dropped: {', '.join(dropped)})" if dropped else ""
    # What the agent looked at, for the review UI and the apply-time
    # staleness check.
    snapshot: dict[str, Any] = {
        k: current for k, (_, current) in scalars.items() if k in fields
    }
    if add or remove:
        snapshot["tags"] = list(doc.tags)
    return await _persist(ctx, p, EntityType.document, document_id, snapshot) + note


async def propose_create_entity(
    ctx: RunContext[AgentDeps],
    entity_type: TaxonomyType,
    name: str,
    match: str | None = None,
    matching_algorithm: int | None = None,
    is_insensitive: bool | None = None,
    assign_to_documents: IntList = None,
) -> str:
    """Propose creating a new tag/correspondent/document_type. FIRST check
    the existing entities (list tools) — never create near-duplicates of
    existing entries. matching_algorithm controls paperless's automatic
    assignment on future documents: 1=any word, 2=all words, 3=exact,
    4=regex, 5=fuzzy (all need a `match` pattern), 6=auto (ML, no
    pattern — the default when omitted), 0=none. Prefer an explicit
    word/exact rule when the document shows a reliable marker (sender
    name, IBAN, letterhead); otherwise leave the default. Optionally
    assign the new entity to documents immediately (JSON array or
    comma-separated string)."""
    _check_matching(match, matching_algorithm)
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
        match=match,
        matching_algorithm=matching_algorithm,
        is_insensitive=is_insensitive,
        assign_to_documents=_int_list(assign_to_documents),
    )
    return await _persist(ctx, p, EntityType(entity_type), None)


async def propose_update_entity(
    ctx: RunContext[AgentDeps],
    entity_type: TaxonomyType,
    entity_id: int,
    name: str | None = None,
    match: str | None = None,
    matching_algorithm: int | None = None,
    is_insensitive: bool | None = None,
) -> str:
    """Propose renaming an entity or fixing its matching rule (see
    propose_create_entity for the matching_algorithm values). Provide
    only the fields to change — values identical to the entity's current
    state are rejected as no-ops."""
    entity = await _require_entity(ctx, entity_type, entity_id)
    changes = {
        k: v
        for k, v in (
            ("name", name),
            ("match", match),
            ("matching_algorithm", matching_algorithm),
            ("is_insensitive", is_insensitive),
        )
        if v is not None and v != getattr(entity, k)
    }
    if not changes:
        raise ModelRetry(
            "Proposal rejected: the proposed values match the entity's "
            "current state. If nothing needs to change, finish without a "
            "proposal."
        )
    # Validate the matching rule the entity would END UP with — but only
    # when it is being touched (plenty of existing entities carry
    # paperless's inert default of algorithm=1 + empty match; a plain
    # rename must not be held hostage to that).
    if "match" in changes or "matching_algorithm" in changes:
        _check_matching(
            changes.get("match", entity.match or None),
            changes.get("matching_algorithm", entity.matching_algorithm),
        )
    p = UpdateEntity(
        entity_type=entity_type,
        entity_id=entity_id,
        name=changes.get("name"),
        match=changes.get("match"),
        matching_algorithm=changes.get("matching_algorithm"),
        is_insensitive=changes.get("is_insensitive"),
    )
    snapshot = {k: getattr(entity, k) for k in changes}
    return await _persist(ctx, p, EntityType(entity_type), entity_id, snapshot)


async def propose_merge_entities(
    ctx: RunContext[AgentDeps],
    entity_type: TaxonomyType,
    source_id: int,
    target_id: int,
) -> str:
    """Propose merging entity `source_id` INTO `target_id`: all documents
    are reassigned to the target, then the source is deleted. The target
    (usually the better-named / larger one) survives."""
    if source_id == target_id:
        raise ModelRetry("Proposal rejected: source and target are the same entity.")
    source = await _require_entity(ctx, entity_type, source_id)
    target = await _require_entity(ctx, entity_type, target_id)
    p = MergeEntities(
        entity_type=entity_type, source_id=source_id, target_id=target_id
    )
    snapshot = {
        "source": {"id": source.id, "name": source.name,
                   "document_count": source.document_count},
        "target": {"id": target.id, "name": target.name,
                   "document_count": target.document_count},
    }
    return await _persist(ctx, p, EntityType(entity_type), source_id, snapshot)


async def propose_delete_entity(
    ctx: RunContext[AgentDeps],
    entity_type: TaxonomyType,
    entity_id: int,
    force: bool = False,
) -> str:
    """Propose deleting an entity. Only for genuinely useless entries
    (empty, or nonsense). Use propose_merge_entities when documents
    should keep an equivalent label. force=True detaches documents first."""
    entity = await _require_entity(ctx, entity_type, entity_id)
    p = DeleteEntity(entity_type=entity_type, entity_id=entity_id, force=force)
    snapshot = {"name": entity.name, "document_count": entity.document_count}
    return await _persist(ctx, p, EntityType(entity_type), entity_id, snapshot)


READ_TOOLS = [
    search_documents,
    find_documents,
    get_document,
    get_document_content,
    list_tags,
    list_correspondents,
    list_document_types,
    find_similar_entities,
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

# Taxonomy agents (tag/correspondent/document_type) review ONE entity:
# rename, fix matching rules, merge into a canonical twin, or delete
# junk. They never touch document metadata or content.
TAXONOMY_AGENT_TOOLS = [
    t for t in ALL_TOOLS if t not in (ocr_document, propose_update_document_metadata)
]
