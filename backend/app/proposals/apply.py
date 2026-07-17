"""Apply engine: turns approved proposals into paperless API calls,
journaling before/after snapshots for undo (DESIGN.md "Apply engine").

Applies ``user_payload`` when present, else the ``agent_payload``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppliedChange, Proposal, ProposalStatus, utcnow
from app.paperless import PaperlessClient, PaperlessError
from app.proposals.schemas import (
    AnyProposal,
    CreateEntity,
    DeleteEntity,
    MergeEntities,
    ReplaceContent,
    UpdateDocumentMetadata,
    UpdateEntity,
    validate_payload,
)


class ApplyError(Exception):
    pass


# Per-taxonomy-type accessor names on PaperlessClient.
_ENTITY_OPS = {
    "tag": ("get_tag", "create_tag", "update_tag", "delete_tag", "tags__id__in"),
    "correspondent": (
        "get_correspondent",
        "create_correspondent",
        "update_correspondent",
        "delete_correspondent",
        "correspondent__id",
    ),
    "document_type": (
        "get_document_type",
        "create_document_type",
        "update_document_type",
        "delete_document_type",
        "document_type__id",
    ),
}

_BULK_SET_METHOD = {
    "correspondent": "set_correspondent",
    "document_type": "set_document_type",
    "storage_path": "set_storage_path",
}


async def apply_proposal(
    paperless: PaperlessClient, db: AsyncSession, proposal: Proposal
) -> AppliedChange | None:
    """Apply a proposal. Returns the journal entry — or ``None`` when
    paperless already matches the proposed state: the proposal is then
    marked ``no_change`` instead of pretending a write happened."""
    if proposal.status not in (ProposalStatus.pending, ProposalStatus.approved):
        raise ApplyError(f"proposal {proposal.id} is {proposal.status}, cannot apply")

    payload = proposal.user_payload or proposal.agent_payload
    typed = validate_payload(payload)

    if await _is_noop(paperless, typed):
        proposal.status = ProposalStatus.no_change
        await db.commit()
        return None

    before, after = await _apply(paperless, typed)

    change = AppliedChange(
        proposal_id=proposal.id, paperless_before=before, paperless_after=after
    )
    proposal.status = ProposalStatus.applied
    db.add(change)
    await db.commit()
    return change


async def _find_existing_entity(paperless: PaperlessClient, entity_type: str, name: str):
    get_list = {
        "tag": paperless.list_tags,
        "correspondent": paperless.list_correspondents,
        "document_type": paperless.list_document_types,
        "storage_path": paperless.list_storage_paths,
    }[entity_type]
    wanted = name.strip().lower()
    for e in await get_list():
        if e.name.strip().lower() == wanted:
            return e
    return None


async def _is_noop(paperless: PaperlessClient, p: AnyProposal) -> bool:  # noqa: C901
    """True when paperless already matches the proposed state (which can
    happen between emit and apply: concurrent sessions, retries, manual
    edits in paperless)."""
    match p:
        case UpdateDocumentMetadata():
            doc = await paperless.get_document(p.document_id)
            provided = p.model_dump(exclude_unset=True)
            for f in ("title", "correspondent", "document_type", "storage_path",
                      "archive_serial_number"):
                if f in provided and provided[f] != getattr(doc, f):
                    return False
            if "created" in provided and provided["created"]:
                if str(provided["created"])[:10] != (doc.created or "")[:10]:
                    return False
            if any(t not in doc.tags for t in p.add_tags):
                return False
            if any(t in doc.tags for t in p.remove_tags):
                return False
            if p.custom_fields:
                existing = {cf.field: cf.value for cf in doc.custom_fields}
                if any(existing.get(k) != v for k, v in p.custom_fields.items()):
                    return False
            return True
        case ReplaceContent():
            doc = await paperless.get_document(p.document_id)
            return doc.content.strip() == p.content.strip()
        case CreateEntity():
            existing = await _find_existing_entity(paperless, p.entity_type, p.name)
            if existing is None:
                return False
            for doc_id in p.assign_to_documents:
                doc = await paperless.get_document(doc_id)
                if p.entity_type == "tag":
                    if existing.id not in doc.tags:
                        return False
                elif getattr(doc, p.entity_type) != existing.id:
                    return False
            return True
        case UpdateEntity():
            get, _, _, _, _ = _ENTITY_OPS[p.entity_type]
            current = await getattr(paperless, get)(p.entity_id)
            fields = _entity_fields(p)
            return bool(fields) and all(
                getattr(current, k, None) == v for k, v in fields.items()
            )
        case MergeEntities() | DeleteEntity():
            get, _, _, _, _ = _ENTITY_OPS[p.entity_type]
            entity_id = p.source_id if isinstance(p, MergeEntities) else p.entity_id
            try:
                await getattr(paperless, get)(entity_id)
            except PaperlessError as e:
                if e.status_code == 404:
                    return True  # already merged away / deleted
                raise
            return False
    return False


async def _apply(
    paperless: PaperlessClient, p: AnyProposal
) -> tuple[dict[str, Any], dict[str, Any]]:
    match p:
        case UpdateDocumentMetadata():
            return await _apply_doc_metadata(paperless, p)
        case ReplaceContent():
            return await _apply_replace_content(paperless, p)
        case CreateEntity():
            return await _apply_create_entity(paperless, p)
        case UpdateEntity():
            return await _apply_update_entity(paperless, p)
        case MergeEntities():
            return await _apply_merge(paperless, p)
        case DeleteEntity():
            return await _apply_delete_entity(paperless, p)
    raise ApplyError(f"unhandled proposal type {type(p).__name__}")


async def _apply_doc_metadata(
    paperless: PaperlessClient, p: UpdateDocumentMetadata
) -> tuple[dict, dict]:
    doc = await paperless.get_document(p.document_id)
    fields: dict[str, Any] = {}
    provided = p.model_dump(exclude_unset=True)
    for f in ("title", "correspondent", "document_type", "storage_path", "created",
              "archive_serial_number"):
        if f in provided:
            fields[f] = provided[f]
    if p.add_tags or p.remove_tags:
        tags = [t for t in doc.tags if t not in p.remove_tags]
        tags += [t for t in p.add_tags if t not in tags]
        fields["tags"] = tags
    if p.custom_fields is not None:
        existing = {cf.field: cf.value for cf in doc.custom_fields}
        existing.update(p.custom_fields)
        fields["custom_fields"] = [
            {"field": k, "value": v} for k, v in existing.items() if v is not None
        ]
    if not fields:
        raise ApplyError("proposal contains no changes")

    before = {"document": doc.model_dump(include={f for f in fields} | {"id", "tags"})}
    updated = await paperless.update_document(p.document_id, **fields)
    after = {"document": updated.model_dump(include={f for f in fields} | {"id", "tags"})}
    return before, after


async def _apply_replace_content(
    paperless: PaperlessClient, p: ReplaceContent
) -> tuple[dict, dict]:
    doc = await paperless.get_document(p.document_id)
    before = {"document": {"id": doc.id, "content": doc.content}}
    updated = await paperless.update_document(p.document_id, content=p.content)
    return before, {"document": {"id": updated.id, "content": updated.content}}


def _entity_fields(p: CreateEntity | UpdateEntity) -> dict[str, Any]:
    provided = p.model_dump(exclude_unset=True)
    fields = {
        k: provided[k]
        for k in ("name", "match", "matching_algorithm", "is_insensitive")
        if k in provided and provided[k] is not None
    }
    fields.update(p.extra)
    return fields


async def _apply_create_entity(
    paperless: PaperlessClient, p: CreateEntity
) -> tuple[dict, dict]:
    if p.entity_type == "storage_path":
        # Needs create_storage_path client support + a `path`; deferred.
        raise ApplyError("storage_path creation not supported yet")
    _, create, _, _, _ = _ENTITY_OPS[p.entity_type]
    # An identically-named entity may have appeared since the proposal
    # (concurrent session): reuse it instead of erroring on a duplicate.
    created = await _find_existing_entity(paperless, p.entity_type, p.name)
    if created is None:
        created = await getattr(paperless, create)(**_entity_fields(p))
    after: dict[str, Any] = {"entity": created.model_dump(), "assigned_documents": []}
    if p.assign_to_documents:
        if p.entity_type == "tag":
            await paperless.bulk_edit_documents(
                p.assign_to_documents, "modify_tags", {"add_tags": [created.id], "remove_tags": []}
            )
        else:
            await paperless.bulk_edit_documents(
                p.assign_to_documents, _BULK_SET_METHOD[p.entity_type], {p.entity_type: created.id}
            )
        after["assigned_documents"] = p.assign_to_documents
    return {"entity": None, "entity_type": p.entity_type}, after


async def _apply_update_entity(
    paperless: PaperlessClient, p: UpdateEntity
) -> tuple[dict, dict]:
    get, _, update, _, _ = _ENTITY_OPS[p.entity_type]
    current = await getattr(paperless, get)(p.entity_id)
    fields = _entity_fields(p)
    if not fields:
        raise ApplyError("proposal contains no changes")
    updated = await getattr(paperless, update)(p.entity_id, **fields)
    return {"entity": current.model_dump()}, {"entity": updated.model_dump()}


async def _docs_referencing(
    paperless: PaperlessClient, entity_type: str, entity_id: int
) -> list[int]:
    if entity_type == "tag":
        page = await paperless.search_documents(tag_ids=[entity_id], page_size=100)
    elif entity_type == "correspondent":
        page = await paperless.search_documents(correspondent_id=entity_id, page_size=100)
    elif entity_type == "document_type":
        page = await paperless.search_documents(document_type_id=entity_id, page_size=100)
    else:
        page = await paperless.search_documents(storage_path_id=entity_id, page_size=100)
    ids = [d.id for d in page.results]
    # `all` carries every matching id regardless of pagination when present.
    if page.all:
        ids = list(page.all)
    elif page.count > len(ids):
        p = 2
        while len(ids) < page.count:
            kwargs = {
                "tag": {"tag_ids": [entity_id]},
                "correspondent": {"correspondent_id": entity_id},
                "document_type": {"document_type_id": entity_id},
                "storage_path": {"storage_path_id": entity_id},
            }[entity_type]
            more = await paperless.search_documents(page=p, page_size=100, **kwargs)
            ids += [d.id for d in more.results]
            p += 1
    return ids


async def _apply_merge(paperless: PaperlessClient, p: MergeEntities) -> tuple[dict, dict]:
    if p.source_id == p.target_id:
        raise ApplyError("merge source and target are identical")
    get, _, _, delete, _ = _ENTITY_OPS[p.entity_type]
    source = await getattr(paperless, get)(p.source_id)
    target = await getattr(paperless, get)(p.target_id)
    doc_ids = await _docs_referencing(paperless, p.entity_type, p.source_id)

    before = {
        "source_entity": source.model_dump(),
        "target_entity": {"id": target.id, "name": target.name},
        "documents_reassigned": doc_ids,
    }
    if doc_ids:
        if p.entity_type == "tag":
            await paperless.bulk_edit_documents(
                doc_ids, "modify_tags", {"add_tags": [p.target_id], "remove_tags": [p.source_id]}
            )
        else:
            await paperless.bulk_edit_documents(
                doc_ids, _BULK_SET_METHOD[p.entity_type], {p.entity_type: p.target_id}
            )
    await getattr(paperless, delete)(p.source_id)
    return before, {
        "merged_into": {"id": target.id, "name": target.name},
        "documents_reassigned": doc_ids,
    }


async def _apply_delete_entity(
    paperless: PaperlessClient, p: DeleteEntity
) -> tuple[dict, dict]:
    get, _, _, delete, _ = _ENTITY_OPS[p.entity_type]
    entity = await getattr(paperless, get)(p.entity_id)
    doc_ids = await _docs_referencing(paperless, p.entity_type, p.entity_id)
    if doc_ids and not p.force:
        raise ApplyError(
            f"{p.entity_type} {entity.name!r} is referenced by {len(doc_ids)} documents; "
            "set force=true to detach and delete"
        )
    before = {"entity": entity.model_dump(), "documents_detached": doc_ids}
    if doc_ids and p.entity_type == "tag":
        await paperless.bulk_edit_documents(
            doc_ids, "modify_tags", {"add_tags": [], "remove_tags": [p.entity_id]}
        )
    elif doc_ids:
        await paperless.bulk_edit_documents(
            doc_ids, _BULK_SET_METHOD[p.entity_type], {p.entity_type: None}
        )
    await getattr(paperless, delete)(p.entity_id)
    return before, {"deleted": True, "documents_detached": doc_ids}


# ----- revert ---------------------------------------------------------


async def revert_change(
    paperless: PaperlessClient, db: AsyncSession, change: AppliedChange
) -> None:
    """Best-effort undo from the journal snapshots."""
    if change.reverted_at is not None:
        raise ApplyError("change already reverted")
    proposal = change.proposal
    typed = validate_payload(proposal.user_payload or proposal.agent_payload)
    before = change.paperless_before

    match typed:
        case UpdateDocumentMetadata() | ReplaceContent():
            doc = dict(before["document"])
            doc_id = doc.pop("id")
            await paperless.update_document(doc_id, **doc)
        case UpdateEntity():
            _, _, update, _, _ = _ENTITY_OPS[typed.entity_type]
            entity = before["entity"]
            await getattr(paperless, update)(
                typed.entity_id,
                **{
                    k: entity[k]
                    for k in ("name", "match", "matching_algorithm", "is_insensitive")
                    if k in entity
                },
            )
        case CreateEntity():
            created_id = change.paperless_after["entity"]["id"]
            _, _, _, delete, _ = _ENTITY_OPS[typed.entity_type]
            await getattr(paperless, delete)(created_id)
        case MergeEntities():
            # Recreate the source entity (new id) and reassign the docs back.
            _, create, _, _, _ = _ENTITY_OPS[typed.entity_type]
            src = before["source_entity"]
            recreated = await getattr(paperless, create)(
                **{
                    k: src[k]
                    for k in ("name", "match", "matching_algorithm", "is_insensitive")
                    if k in src and src[k] is not None
                }
            )
            doc_ids = before["documents_reassigned"]
            if doc_ids:
                if typed.entity_type == "tag":
                    await paperless.bulk_edit_documents(
                        doc_ids,
                        "modify_tags",
                        {"add_tags": [recreated.id], "remove_tags": [typed.target_id]},
                    )
                else:
                    await paperless.bulk_edit_documents(
                        doc_ids, _BULK_SET_METHOD[typed.entity_type],
                        {typed.entity_type: recreated.id},
                    )
        case DeleteEntity():
            _, create, _, _, _ = _ENTITY_OPS[typed.entity_type]
            src = before["entity"]
            recreated = await getattr(paperless, create)(
                **{
                    k: src[k]
                    for k in ("name", "match", "matching_algorithm", "is_insensitive")
                    if k in src and src[k] is not None
                }
            )
            doc_ids = before.get("documents_detached", [])
            if doc_ids:
                if typed.entity_type == "tag":
                    await paperless.bulk_edit_documents(
                        doc_ids, "modify_tags",
                        {"add_tags": [recreated.id], "remove_tags": []},
                    )
                else:
                    await paperless.bulk_edit_documents(
                        doc_ids, _BULK_SET_METHOD[typed.entity_type],
                        {typed.entity_type: recreated.id},
                    )
        case _:
            raise ApplyError(f"revert not supported for {typed.kind}")

    change.reverted_at = utcnow()
    await db.commit()
