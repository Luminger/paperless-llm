"""Typed views over the paperless-ngx REST API.

Models are intentionally permissive (``extra="allow"``): they carry the
fields the app relies on and tolerate everything else, so paperless
version drift doesn't break parsing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Permissive(BaseModel):
    model_config = ConfigDict(extra="allow")


class Page[T](BaseModel):
    count: int
    next: str | None = None
    previous: str | None = None
    results: list[T]
    # Present on ?query= full-text searches.
    all: list[int] | None = None


class MatchingModel(_Permissive):
    """Shared shape of tag/correspondent/document_type/storage_path."""

    id: int
    name: str
    slug: str | None = None
    match: str = ""
    matching_algorithm: int = 0
    is_insensitive: bool = True
    document_count: int | None = None


class Tag(MatchingModel):
    color: str | None = None
    is_inbox_tag: bool = False


class Correspondent(MatchingModel):
    last_correspondence: str | None = None


class DocumentType(MatchingModel):
    pass


class StoragePath(MatchingModel):
    path: str = ""


class CustomField(_Permissive):
    id: int
    name: str
    data_type: str
    # select fields carry their options here: {"select_options":
    # [{"id": "...", "label": "..."}]}; paperless sends null for
    # everything else.
    extra_data: dict[str, Any] | None = None


class CustomFieldInstance(_Permissive):
    field: int
    value: Any = None


class Document(_Permissive):
    id: int
    title: str = ""
    content: str = ""
    tags: list[int] = []
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    created: str | None = None
    added: str | None = None
    modified: str | None = None
    archive_serial_number: int | None = None
    original_file_name: str | None = None
    checksum: str | None = None  # only present via /api/documents/{id}/metadata/
    custom_fields: list[CustomFieldInstance] = []
    # Search-only fields.
    search_hit: dict[str, Any] | None = None
