from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.proposals.schemas import (
    MergeEntities,
    UpdateDocumentMetadata,
    dump_payload,
    validate_payload,
)


def test_roundtrip_update_document_metadata():
    p = UpdateDocumentMetadata(
        document_id=42, title="Telarko Rechnung März 2024", add_tags=[1, 2]
    )
    payload = dump_payload(p)
    # exclude_unset: fields never provided are absent...
    assert "correspondent" not in payload
    assert payload["kind"] == "update_document_metadata"
    restored = validate_payload(payload)
    assert isinstance(restored, UpdateDocumentMetadata)
    assert restored.title == "Telarko Rechnung März 2024"
    assert restored.add_tags == [1, 2]


def test_explicit_none_clears_field():
    p = UpdateDocumentMetadata.model_validate(
        {"document_id": 1, "correspondent": None}
    )
    payload = dump_payload(p)
    # ...but an explicit None survives serialization (proposed clear).
    assert "correspondent" in payload and payload["correspondent"] is None


def test_discriminator_dispatch():
    p = validate_payload(
        {"kind": "merge_entities", "entity_type": "tag", "source_id": 1, "target_id": 2}
    )
    assert isinstance(p, MergeEntities)


def test_unknown_kind_rejected():
    with pytest.raises(ValidationError):
        validate_payload({"kind": "drop_database", "entity_type": "tag"})


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        validate_payload(
            {"kind": "replace_content", "document_id": 1, "content": "x", "bogus": True}
        )
