"""Typed proposal payloads — the only vocabulary agents may use to
effect change, and the shape the review UI edits.

Stored in ``Proposal.agent_payload`` / ``Proposal.user_payload`` as JSON
(``model_dump(exclude_unset=True)`` so "not proposed" is distinguishable
from "proposed to clear").
"""

from __future__ import annotations

import enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class ProposalKind(enum.StrEnum):
    update_document_metadata = "update_document_metadata"
    replace_content = "replace_content"
    create_entity = "create_entity"
    update_entity = "update_entity"
    merge_entities = "merge_entities"
    delete_entity = "delete_entity"


TaxonomyType = Literal["tag", "correspondent", "document_type", "storage_path"]


class _ProposalBase(BaseModel):
    # The agent's explanation lives in its prose summary on the
    # timeline — proposals carry data only.
    model_config = ConfigDict(extra="forbid")


class UpdateDocumentMetadata(_ProposalBase):
    """Partial metadata update for one document. Only set fields are
    applied; explicit ``None`` clears a field."""

    kind: Literal[ProposalKind.update_document_metadata] = (
        ProposalKind.update_document_metadata
    )
    document_id: int
    title: str | None = None
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    created: str | None = None  # ISO date
    archive_serial_number: int | None = None
    add_tags: list[int] = Field(default_factory=list)
    remove_tags: list[int] = Field(default_factory=list)
    # {custom_field_id: value}; explicit None clears the field value.
    custom_fields: dict[int, Any] | None = None


class ReplaceContent(_ProposalBase):
    """Replace the paperless ``content`` (OCR text) of a document."""

    kind: Literal[ProposalKind.replace_content] = ProposalKind.replace_content
    document_id: int
    content: str
    # Similarity between proposed and existing content at proposal time.
    similarity_to_existing: float | None = None


class CreateEntity(_ProposalBase):
    kind: Literal[ProposalKind.create_entity] = ProposalKind.create_entity
    entity_type: TaxonomyType
    name: str
    match: str | None = None
    matching_algorithm: int | None = None
    is_insensitive: bool | None = None
    # tag color / storage_path path etc.
    extra: dict[str, Any] = Field(default_factory=dict)
    # Documents to attach the new entity to right after creation.
    assign_to_documents: list[int] = Field(default_factory=list)


class UpdateEntity(_ProposalBase):
    kind: Literal[ProposalKind.update_entity] = ProposalKind.update_entity
    entity_type: TaxonomyType
    entity_id: int
    name: str | None = None
    match: str | None = None
    matching_algorithm: int | None = None
    is_insensitive: bool | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class MergeEntities(_ProposalBase):
    """Reassign all documents from ``source_id`` to ``target_id``, then
    delete the source entity."""

    kind: Literal[ProposalKind.merge_entities] = ProposalKind.merge_entities
    entity_type: TaxonomyType
    source_id: int
    target_id: int


class DeleteEntity(_ProposalBase):
    """Delete a taxonomy entity. Refused if documents still reference it
    unless ``force`` (which detaches them first)."""

    kind: Literal[ProposalKind.delete_entity] = ProposalKind.delete_entity
    entity_type: TaxonomyType
    entity_id: int
    force: bool = False


AnyProposal = Annotated[
    UpdateDocumentMetadata
    | ReplaceContent
    | CreateEntity
    | UpdateEntity
    | MergeEntities
    | DeleteEntity,
    Field(discriminator="kind"),
]

_adapter: TypeAdapter[AnyProposal] = TypeAdapter(AnyProposal)


def validate_payload(payload: dict[str, Any]) -> AnyProposal:
    return _adapter.validate_python(payload)


def dump_payload(proposal: AnyProposal) -> dict[str, Any]:
    return proposal.model_dump(mode="json", exclude_unset=True) | {
        "kind": str(proposal.kind)
    }
