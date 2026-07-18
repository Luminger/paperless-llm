"""THE taxonomy registry: one place that knows, per entity type, which
client methods list/get/create/update/delete it and how documents
reference it. Everything that used to hand-roll this map (apply engine,
agent deps, entity index, routes, jobs) consumes this instead — adding
a taxonomy type is a one-entry change.

Methods are stored unbound (``PaperlessClient.list_tags``) and called
as ``spec.list(client)`` — typos fail at import, not at runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.paperless.client import PaperlessClient


@dataclass(frozen=True)
class TaxonomySpec:
    type: str
    list: Callable[..., Any]
    get: Callable[..., Any]
    create: Callable[..., Any]
    update: Callable[..., Any]
    delete: Callable[..., Any]
    # search_documents(**{search_kwarg: value}) finds referencing docs.
    search_kwarg: str
    # Tags are many-per-document; the others are single-valued.
    many: bool = False

    def search_filter(self, entity_id: int) -> dict[str, Any]:
        return {self.search_kwarg: [entity_id] if self.many else entity_id}


TAXONOMY: dict[str, TaxonomySpec] = {
    "tag": TaxonomySpec(
        type="tag",
        list=PaperlessClient.list_tags,
        get=PaperlessClient.get_tag,
        create=PaperlessClient.create_tag,
        update=PaperlessClient.update_tag,
        delete=PaperlessClient.delete_tag,
        search_kwarg="tag_ids",
        many=True,
    ),
    "correspondent": TaxonomySpec(
        type="correspondent",
        list=PaperlessClient.list_correspondents,
        get=PaperlessClient.get_correspondent,
        create=PaperlessClient.create_correspondent,
        update=PaperlessClient.update_correspondent,
        delete=PaperlessClient.delete_correspondent,
        search_kwarg="correspondent_id",
    ),
    "document_type": TaxonomySpec(
        type="document_type",
        list=PaperlessClient.list_document_types,
        get=PaperlessClient.get_document_type,
        create=PaperlessClient.create_document_type,
        update=PaperlessClient.update_document_type,
        delete=PaperlessClient.delete_document_type,
        search_kwarg="document_type_id",
    ),
    "storage_path": TaxonomySpec(
        type="storage_path",
        list=PaperlessClient.list_storage_paths,
        get=PaperlessClient.get_storage_path,
        create=PaperlessClient.create_storage_path,
        update=PaperlessClient.update_storage_path,
        delete=PaperlessClient.delete_storage_path,
        search_kwarg="storage_path_id",
    ),
}

TAXONOMY_TYPES = tuple(TAXONOMY)
