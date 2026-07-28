"""Apply engine: turns reviewed proposals into paperless API calls,
journaling before/after snapshots for undo (DESIGN.md "Apply engine").

Applies ``user_payload`` when present, else the ``agent_payload``.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppliedChange, Proposal, ProposalStatus, utcnow
from app.paperless import PaperlessClient, PaperlessError
from app.paperless.taxonomy import TAXONOMY
from app.proposals.kinds import ENTITY_RULE_FIELDS, MATCHING_AUTO
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
from app.services.actor import current_actor
from app.services.audit import record


class ApplyError(Exception):
    pass


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
    # Atomic claim: flip pending -> applying in one UPDATE so two
    # concurrent applies can never both pass a check-then-act gate.
    # (applied_changes.proposal_id is UNIQUE as a second line of
    # defense.)
    claimed = await db.execute(
        sa_update(Proposal)
        .where(Proposal.id == proposal.id, Proposal.status == ProposalStatus.pending)
        .values(status=ProposalStatus.applying)
    )
    if claimed.rowcount == 0:
        raise ApplyError(f"proposal {proposal.id} is {proposal.status.value}, cannot apply")
    await db.commit()
    await db.refresh(proposal)

    try:
        return await _apply_claimed(paperless, db, proposal)
    except Exception:
        # Nothing was journaled: release the claim so the user can retry.
        proposal.status = ProposalStatus.pending
        await db.commit()
        raise


async def _apply_claimed(
    paperless: PaperlessClient, db: AsyncSession, proposal: Proposal
) -> AppliedChange | None:
    payload = proposal.user_payload or proposal.agent_payload
    typed = validate_payload(payload)

    if await _is_noop(paperless, typed):
        proposal.status = ProposalStatus.no_change
        await record(
            db, "proposal", "no_change",
            proposal_id=proposal.id, proposal_kind=proposal.kind,
            session_id=proposal.session_id,
        )
        await db.commit()
        return None

    # Optimistic concurrency: paperless has no revisions, so we verify
    # value-by-value that the fields the agent looked at (base_snapshot,
    # captured at proposal time) are unchanged. A field that already
    # equals the proposed target doesn't conflict — it converged.
    conflicts = await _snapshot_conflicts(paperless, typed, proposal.base_snapshot)
    if conflicts:
        raise ApplyError(
            "paperless changed since this was proposed — review before applying: "
            + "; ".join(conflicts)
        )

    # AUDIT API-F14 (known window): the paperless write below lands
    # BEFORE the journal row commits. A crash in between releases the
    # claim; the retry then usually sees _is_noop and the change ends up
    # unrevertible/unattributed. Accepted for now — a pre-write intent
    # row would close it at the cost of journal noise.
    before, after = await _apply(paperless, typed)

    change = AppliedChange(
        proposal_id=proposal.id,
        actor=current_actor(),
        paperless_before=before,
        paperless_after=after,
    )
    proposal.status = ProposalStatus.applied
    db.add(change)
    await record(
        db, "proposal", "applied",
        proposal_id=proposal.id, proposal_kind=proposal.kind,
        session_id=proposal.session_id,
        entity_type=proposal.entity_type.value if proposal.entity_type else None,
        entity_id=proposal.entity_id,
        diff=audit_diff(before, after),
    )
    await db.commit()
    return change


async def _find_existing_entity(paperless: PaperlessClient, entity_type: str, name: str):
    wanted = name.strip().lower()
    for e in await TAXONOMY[entity_type].list(paperless):
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
            spec = TAXONOMY[p.entity_type]
            current = await spec.get(paperless, p.entity_id)
            fields = _entity_fields(p)
            return bool(fields) and all(
                getattr(current, k, None) == v for k, v in fields.items()
            )
        case MergeEntities() | DeleteEntity():
            spec = TAXONOMY[p.entity_type]
            entity_id = p.source_id if isinstance(p, MergeEntities) else p.entity_id
            try:
                await spec.get(paperless, entity_id)
            except PaperlessError as e:
                if e.status_code == 404:
                    return True  # already merged away / deleted
                raise
            return False
    return False


def _fmt(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def _short(v: Any) -> Any:
    if isinstance(v, str) and len(v) > 200:
        return v[:200] + f" …[{len(v)} chars total]"
    return v


def audit_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """From-→-to per changed field, derived from the journal snapshots
    (long strings truncated — the full snapshots stay in the journal)."""
    b = before.get("document") or before.get("entity") or before
    a = after.get("document") or after.get("entity") or after
    if not isinstance(b, dict):
        b = {}
    if not isinstance(a, dict):
        a = {}
    out: dict[str, Any] = {}
    for k in sorted(set(b) | set(a)):
        bv, av = b.get(k), a.get(k)
        if bv != av:
            out[k] = {"from": _short(bv), "to": _short(av)}
    return out


async def _snapshot_conflicts(  # noqa: C901
    paperless: PaperlessClient, p: AnyProposal, snapshot: dict[str, Any] | None
) -> list[str]:
    if not snapshot:
        return []  # pre-snapshot proposals: no staleness check possible
    conflicts: list[str] = []
    match p:
        case UpdateDocumentMetadata():
            doc = await paperless.get_document(p.document_id)
            provided = p.model_dump(exclude_unset=True)
            for k, was in snapshot.items():
                if k == "tags":
                    if sorted(doc.tags) != sorted(was):
                        conflicts.append(f"tags: were {_fmt(was)}, now {_fmt(doc.tags)}")
                    continue
                now = getattr(doc, k, None)
                if k == "created":
                    now = (now or "")[:10] or None
                    was = (was or "")[:10] or None
                if now != was and now != provided.get(k):
                    conflicts.append(f"{k}: was {_fmt(was)}, now {_fmt(now)}")
        case UpdateEntity():
            spec = TAXONOMY[p.entity_type]
            current = await spec.get(paperless, p.entity_id)
            provided = p.model_dump(exclude_unset=True)
            for k, was in snapshot.items():
                now = getattr(current, k, None)
                if now != was and now != provided.get(k):
                    conflicts.append(f"{k}: was {_fmt(was)}, now {_fmt(now)}")
        case MergeEntities():
            spec = TAXONOMY[p.entity_type]
            source = await spec.get(paperless, p.source_id)
            try:
                target = await spec.get(paperless, p.target_id)
            except PaperlessError as e:
                if e.status_code == 404:
                    return [f"merge target #{p.target_id} no longer exists"]
                raise
            if source.name != snapshot.get("source", {}).get("name"):
                conflicts.append(
                    f"source was {_fmt(snapshot['source']['name'])}, now {_fmt(source.name)}"
                )
            if target.name != snapshot.get("target", {}).get("name"):
                conflicts.append(
                    f"target was {_fmt(snapshot['target']['name'])}, now {_fmt(target.name)}"
                )
        case DeleteEntity():
            spec = TAXONOMY[p.entity_type]
            current = await spec.get(paperless, p.entity_id)
            if current.name != snapshot.get("name"):
                conflicts.append(
                    f"name was {_fmt(snapshot.get('name'))}, now {_fmt(current.name)}"
                )
            was_count = snapshot.get("document_count")
            if was_count is not None and current.document_count != was_count:
                conflicts.append(
                    f"document count was {was_count}, now {current.document_count}"
                )
    return conflicts


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

    # AUDIT API-F6: snapshot ONLY the fields this proposal touches —
    # `tags` rides along only when tags were actually proposed, so a
    # title-only revert can never clobber later tag edits.
    snap = set(fields) | {"id"}
    before = {"document": doc.model_dump(include=snap)}
    updated = await paperless.update_document(p.document_id, **fields)
    after = {"document": updated.model_dump(include=snap)}
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
        for k in ENTITY_RULE_FIELDS
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
    spec = TAXONOMY[p.entity_type]
    # An identically-named entity may have appeared since the proposal
    # (concurrent session): reuse it instead of erroring on a duplicate.
    existing = await _find_existing_entity(paperless, p.entity_type, p.name)
    reused = existing is not None
    if existing is not None:
        created = existing
    else:
        fields = _entity_fields(p)
        # Entities we create default to auto (ML) matching so paperless's
        # own classifier keeps learning and pre-assigning — the agent can
        # still propose an explicit rule instead.
        if "match" not in fields:
            fields.setdefault("matching_algorithm", MATCHING_AUTO)
        created = await spec.create(paperless, **fields)
    # AUDIT API-F1: an honest journal — when we REUSED an entity that
    # appeared since the proposal, `before` snapshots it and `reused`
    # marks it, so revert can never delete something we didn't create.
    after: dict[str, Any] = {
        "entity": created.model_dump(),
        "assigned_documents": [],
        "reused": reused,
    }
    if p.assign_to_documents:
        # Reinspection (API-F1 follow-up): journal only the documents
        # that actually GAIN the entity. For a REUSED entity some of the
        # listed docs may already carry it (a human's assignment) —
        # revert must undo OUR assignments only, so pre-check which docs
        # already have it. Freshly created entities can't be on anything.
        gained = p.assign_to_documents
        if reused:
            already: set[int] = set()
            for i in range(0, len(p.assign_to_documents), 100):
                chunk = p.assign_to_documents[i : i + 100]
                kw: dict[str, Any] = {"document_ids": chunk, "page_size": 100}
                if p.entity_type == "tag":
                    kw["tag_ids"] = [created.id]
                elif p.entity_type == "correspondent":
                    kw["correspondent_id"] = created.id
                elif p.entity_type == "document_type":
                    kw["document_type_id"] = created.id
                else:
                    kw["storage_path_id"] = created.id
                page_r = await paperless.search_documents(**kw)
                already.update(d.id for d in page_r.results)
            gained = [d for d in p.assign_to_documents if d not in already]
        if p.entity_type == "tag":
            await paperless.bulk_edit_documents(
                p.assign_to_documents, "modify_tags", {"add_tags": [created.id], "remove_tags": []}
            )
        else:
            await paperless.bulk_edit_documents(
                p.assign_to_documents, _BULK_SET_METHOD[p.entity_type], {p.entity_type: created.id}
            )
        after["assigned_documents"] = gained
    return {
        "entity": existing.model_dump() if existing is not None else None,
        "entity_type": p.entity_type,
    }, after


async def _apply_update_entity(
    paperless: PaperlessClient, p: UpdateEntity
) -> tuple[dict, dict]:
    spec = TAXONOMY[p.entity_type]
    current = await spec.get(paperless, p.entity_id)
    fields = _entity_fields(p)
    if not fields:
        raise ApplyError("proposal contains no changes")
    updated = await spec.update(paperless, p.entity_id, **fields)
    return {"entity": current.model_dump()}, {"entity": updated.model_dump()}


async def _docs_referencing(
    paperless: PaperlessClient, entity_type: str, entity_id: int
) -> list[int]:
    kwargs = TAXONOMY[entity_type].search_filter(entity_id)
    page = await paperless.search_documents(page_size=100, **kwargs)
    ids = [d.id for d in page.results]
    # `all` carries every matching id regardless of pagination when present.
    if page.all:
        ids = list(page.all)
    elif page.count > len(ids):
        # Bounded by pages, not by count: documents deleted mid-iteration
        # must not turn this into an out-of-range 404 loop.
        total_pages = -(-page.count // 100)
        for p in range(2, total_pages + 1):
            more = await paperless.search_documents(page=p, page_size=100, **kwargs)
            if not more.results:
                break
            ids += [d.id for d in more.results]
    return ids


async def _apply_merge(paperless: PaperlessClient, p: MergeEntities) -> tuple[dict, dict]:
    if p.source_id == p.target_id:
        raise ApplyError("merge source and target are identical")
    spec = TAXONOMY[p.entity_type]
    source = await spec.get(paperless, p.source_id)
    target = await spec.get(paperless, p.target_id)
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
    await spec.delete(paperless, p.source_id)
    return before, {
        "merged_into": {"id": target.id, "name": target.name},
        "documents_reassigned": doc_ids,
    }


async def _apply_delete_entity(
    paperless: PaperlessClient, p: DeleteEntity
) -> tuple[dict, dict]:
    spec = TAXONOMY[p.entity_type]
    entity = await spec.get(paperless, p.entity_id)
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
    await spec.delete(paperless, p.entity_id)
    return before, {"deleted": True, "documents_detached": doc_ids}


# ----- revert ---------------------------------------------------------


async def revert_is_noop(
    paperless: PaperlessClient, proposal: Proposal, change: AppliedChange
) -> bool:  # noqa: C901
    """True when paperless already matches the state this revert would
    restore — reverting would write nothing (e.g. someone already
    changed it back, manually or via another revert)."""
    typed = validate_payload(proposal.user_payload or proposal.agent_payload)
    before = change.paperless_before

    match typed:
        case UpdateDocumentMetadata() | ReplaceContent():
            doc = await paperless.get_document(typed.document_id)
            saved = dict(before.get("document") or {})
            saved.pop("id", None)
            for k, v in saved.items():
                cur = getattr(doc, k, None)
                if k == "tags":
                    if sorted(cur or []) != sorted(v or []):
                        return False
                elif k == "created":
                    if (cur or "")[:10] != (v or "")[:10]:
                        return False
                elif k == "content":
                    if (cur or "").strip() != (v or "").strip():
                        return False
                elif k == "custom_fields":
                    cur_cf = [cf.model_dump() for cf in doc.custom_fields]
                    if cur_cf != v:
                        return False
                elif cur != v:
                    return False
            return True
        case CreateEntity():
            created_id = (change.paperless_after.get("entity") or {}).get("id")
            if created_id is None:
                return False
            spec = TAXONOMY[typed.entity_type]
            try:
                await spec.get(paperless, created_id)
            except PaperlessError as e:
                if e.status_code == 404:
                    return True  # already deleted — nothing to revert
                raise
            if _entity_was_reused(change):
                # We only assigned documents; with none assigned there
                # is nothing of ours to undo.
                return not change.paperless_after.get("assigned_documents")
            return False
        case UpdateEntity():
            spec = TAXONOMY[typed.entity_type]
            current = await spec.get(paperless, typed.entity_id)
            ent = before.get("entity") or {}
            return all(
                getattr(current, k, None) == ent[k]
                for k in ENTITY_RULE_FIELDS
                if k in ent
            )
        case MergeEntities():
            src = before.get("source_entity") or {}
            return (
                src.get("name") is not None
                and await _find_existing_entity(paperless, typed.entity_type, src["name"])
                is not None
            )
        case DeleteEntity():
            ent = before.get("entity") or {}
            return (
                ent.get("name") is not None
                and await _find_existing_entity(paperless, typed.entity_type, ent["name"])
                is not None
            )
    return False


def _entity_was_reused(change: AppliedChange) -> bool:
    """AUDIT API-F1: pre-`reused`-flag journals are recognized by the
    honest `before` snapshot of the reused entity."""
    return bool(change.paperless_after.get("reused")) or (
        (change.paperless_before or {}).get("entity") is not None
    )


async def revert_change(
    paperless: PaperlessClient, db: AsyncSession, change: AppliedChange
) -> None:
    """Best-effort undo from the journal snapshots.

    AUDIT API-F5: the revert is CLAIMED first (guarded UPDATE on
    ``reverted_at``) so two concurrent reverts can never both execute
    the paperless writes; the claim is released on any failure."""
    claimed = await db.execute(
        sa_update(AppliedChange)
        .where(AppliedChange.id == change.id, AppliedChange.reverted_at.is_(None))
        .values(reverted_at=utcnow())
    )
    if claimed.rowcount == 0:
        raise ApplyError("change already reverted")
    await db.commit()
    await db.refresh(change)
    try:
        await _revert_claimed(paperless, db, change)
    except Exception:
        change.reverted_at = None  # release the claim
        await db.commit()
        raise


async def _revert_claimed(
    paperless: PaperlessClient, db: AsyncSession, change: AppliedChange
) -> None:
    proposal = change.proposal
    if await revert_is_noop(paperless, proposal, change):
        raise ApplyError(
            "paperless already matches the state this revert would restore — "
            "there is nothing to undo"
        )
    typed = validate_payload(proposal.user_payload or proposal.agent_payload)
    before = change.paperless_before

    match typed:
        case UpdateDocumentMetadata() | ReplaceContent():
            doc = dict(before["document"])
            doc_id = doc.pop("id")
            saved_tags = doc.pop("tags", None)
            saved_cf = doc.pop("custom_fields", None)
            after_doc = change.paperless_after.get("document") or {}
            touched_cf = (
                getattr(typed, "custom_fields", None)
                if isinstance(typed, UpdateDocumentMetadata)
                else None
            )
            if saved_cf is not None and touched_cf:
                # Reinspection (API-F6 follow-up): custom_fields revert as
                # a DELTA too — restore only the fields THIS apply set,
                # on top of the doc's CURRENT list, so field edits made
                # in paperless since the apply survive.
                before_map = {cf["field"]: cf["value"] for cf in saved_cf}
                current_cf = (await paperless.get_document(doc_id)).custom_fields
                merged = {cf.field: cf.value for cf in current_cf}
                for f in touched_cf:
                    if f in before_map:
                        merged[f] = before_map[f]
                    else:
                        merged.pop(f, None)
                doc["custom_fields"] = [
                    {"field": k, "value": v} for k, v in merged.items() if v is not None
                ]
            if saved_tags is not None and "tags" in after_doc:
                # AUDIT API-F6: revert tags as a DELTA of what THIS apply
                # changed — never overwrite with the full snapshot (tag
                # edits made in paperless since must survive).
                added = set(after_doc["tags"]) - set(saved_tags)
                removed = set(saved_tags) - set(after_doc["tags"])
                current = (await paperless.get_document(doc_id)).tags
                doc["tags"] = [t for t in current if t not in added] + [
                    t for t in removed if t not in current
                ]
            await paperless.update_document(doc_id, **doc)
        case UpdateEntity():
            spec = TAXONOMY[typed.entity_type]
            entity = before["entity"]
            await spec.update(paperless, 
                typed.entity_id,
                **{
                    k: entity[k]
                    for k in ENTITY_RULE_FIELDS
                    if k in entity
                },
            )
        case CreateEntity():
            created_id = change.paperless_after["entity"]["id"]
            spec = TAXONOMY[typed.entity_type]
            if _entity_was_reused(change):
                # AUDIT API-F1: we never created this entity — deleting
                # it would take it away from every document using it.
                # Undo only OUR document assignments.
                doc_ids = change.paperless_after.get("assigned_documents") or []
                if doc_ids:
                    if typed.entity_type == "tag":
                        await paperless.bulk_edit_documents(
                            doc_ids, "modify_tags",
                            {"add_tags": [], "remove_tags": [created_id]},
                        )
                    else:
                        await paperless.bulk_edit_documents(
                            doc_ids, _BULK_SET_METHOD[typed.entity_type],
                            {typed.entity_type: None},
                        )
            else:
                await spec.delete(paperless, created_id)
        case MergeEntities():
            # Recreate the source entity (new id) and reassign the docs back.
            spec = TAXONOMY[typed.entity_type]
            src = before["source_entity"]
            recreated = await spec.create(paperless, 
                **{
                    k: src[k]
                    for k in ENTITY_RULE_FIELDS
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
            spec = TAXONOMY[typed.entity_type]
            src = before["entity"]
            recreated = await spec.create(paperless, 
                **{
                    k: src[k]
                    for k in ENTITY_RULE_FIELDS
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

    # reverted_at was already set by the claim.
    await record(
        db, "proposal", "reverted",
        proposal_id=proposal.id, proposal_kind=proposal.kind,
        session_id=proposal.session_id,
        # Reverting goes applied-state -> pre-apply-state.
        diff=audit_diff(change.paperless_after, change.paperless_before),
    )
    await db.commit()
