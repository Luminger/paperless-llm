"""Async paperless-ngx REST API client.

The single place in the codebase that talks to paperless. Agent tools,
the apply engine, the seeder, and the indexer all go through this.

API reference: https://docs.paperless-ngx.com/api/
"""

from __future__ import annotations

from typing import Any

import httpx

from app.paperless.schemas import (
    Correspondent,
    CustomField,
    Document,
    DocumentType,
    Page,
    StoragePath,
    Tag,
)

# ----- fetch transparency -------------------------------------------
# Module-level registry of the app's GET traffic to paperless, keyed by
# resource. Covers EVERY consumer in this process (UI proxy routes,
# agent tools, pipeline stages) — "when did we last fetch X and is a
# fetch running right now" is answered truthfully, not per-page.
fetch_status: dict[str, dict] = {}

_RESOURCES = (
    ("/api/documents", "documents"),
    ("/api/tags", "tags"),
    ("/api/correspondents", "correspondents"),
    ("/api/document_types", "document_types"),
    ("/api/storage_paths", "storage_paths"),
)


def _classify(path: str) -> str | None:
    for prefix, name in _RESOURCES:
        if path.startswith(prefix):
            return name
    return None


def _track_start(resource: str) -> dict:
    entry = fetch_status.setdefault(
        resource, {"in_flight": 0, "last_fetched_at": None, "last_error": None}
    )
    entry["in_flight"] += 1
    return entry


class PaperlessError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class PaperlessClient:
    """Authenticates with a token, or lazily obtains one via
    ``username``/``password`` (POST /api/token/) when no token is given —
    useful for throwaway instances whose tokens don't exist until first
    boot (e.g. the containerized playground)."""

    def __init__(
        self,
        base_url: str,
        token: str = "",
        timeout: float = 30.0,
        *,
        username: str = "",
        password: str = "",
        verify_tls: bool = True,
    ):
        self._username = username
        self._password = password
        self._authed = bool(token)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Token {token}"} if token else {},
            timeout=timeout,
            follow_redirects=True,
            # Self-signed paperless setups can opt out of verification
            # ([paperless] verify_tls = false) — on by default.
            verify=verify_tls,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> PaperlessClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ----- plumbing ---------------------------------------------------

    async def _ensure_auth(self) -> None:
        if self._authed:
            return
        if not (self._username and self._password):
            raise PaperlessError(
                "paperless auth not configured: provide a token or username/password"
            )
        try:
            resp = await self._client.post(
                "/api/token/",
                data={"username": self._username, "password": self._password},
            )
        except httpx.HTTPError as e:
            raise PaperlessError(f"token fetch failed: {e}") from e
        if resp.status_code >= 400:
            raise PaperlessError(
                f"token fetch failed: {resp.status_code}: {resp.text[:300]}",
                status_code=resp.status_code,
            )
        self._client.headers["Authorization"] = f"Token {resp.json()['token']}"
        self._authed = True

    def _audit_call(self, method: str, url: str, status: int | None) -> None:
        """Every paperless call lands in the audit trail (via the async
        buffer — the client has no DB session), attributed to the actor
        that caused it. Auth traffic is internal and skipped."""
        if url.startswith("/api/token"):
            return
        from datetime import UTC, datetime

        from app.services.actor import current_actor
        from app.services.paperless_log import enqueue

        enqueue(
            {
                "ts": datetime.now(UTC),
                "action": "fetch" if method in ("GET", "HEAD") else "write",
                "actor": current_actor(),
                "method": method,
                "path": url,
                "resource": _classify(url),
                "status": status,
            }
        )

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        await self._ensure_auth()
        resource = _classify(url) if method == "GET" else None
        entry = _track_start(resource) if resource else None
        status: int | None = None
        try:
            resp = await self._client.request(method, url, **kwargs)
            status = resp.status_code
        except httpx.HTTPError as e:
            if entry is not None:
                entry["last_error"] = str(e)[:200]
            raise PaperlessError(f"paperless request failed: {e}") from e
        finally:
            if entry is not None:
                entry["in_flight"] -= 1
            self._audit_call(method, url, status)
        if resp.status_code >= 400:
            if entry is not None:
                entry["last_error"] = f"HTTP {resp.status_code}"
            raise PaperlessError(
                f"{method} {url} -> {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
            )
        if entry is not None:
            from datetime import UTC, datetime

            entry["last_fetched_at"] = datetime.now(UTC).isoformat()
            entry["last_error"] = None
        return resp

    async def _get_json(self, url: str, **params: Any) -> Any:
        resp = await self._request(
            "GET", url, params={k: v for k, v in params.items() if v is not None}
        )
        return resp.json()

    async def _drain[T](self, url: str, model: type[T], **params: Any) -> list[T]:
        """Follow pagination until exhausted."""
        out: list[T] = []
        params = {"page_size": 100, **params}
        page_url: str | None = url
        while page_url:
            data = await self._get_json(page_url, **params)
            out.extend(model.model_validate(r) for r in data["results"])  # type: ignore[attr-defined]
            page_url = data.get("next")
            params = {}  # `next` already carries the query string
        return out

    # ----- documents --------------------------------------------------

    async def search_documents(
        self,
        query: str | None = None,
        *,
        title_contains: str | None = None,
        tag_ids: list[int] | None = None,
        tags_any: list[int] | None = None,
        tags_none: bool | None = None,
        correspondent_id: int | None = None,
        correspondent_ids: list[int] | None = None,
        correspondent_none: bool | None = None,
        document_type_id: int | None = None,
        document_type_ids: list[int] | None = None,
        document_type_none: bool | None = None,
        storage_path_id: int | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        added_after: str | None = None,
        ordering: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> Page[Document]:
        """Full-text search (``query``) and/or field filters."""
        params: dict[str, Any] = {
            "query": query,
            "title__icontains": title_contains,
            # __all = document carries ALL of these (agent/scope use);
            # __in = ANY of these (multiselect filters).
            "tags__id__all": ",".join(map(str, tag_ids)) if tag_ids else None,
            "tags__id__in": ",".join(map(str, tags_any)) if tags_any else None,
            "is_tagged": None if tags_none is None else (not tags_none),
            "correspondent__id": correspondent_id,
            "correspondent__id__in": (
                ",".join(map(str, correspondent_ids)) if correspondent_ids else None
            ),
            "correspondent__isnull": correspondent_none,
            "document_type__id": document_type_id,
            "document_type__id__in": (
                ",".join(map(str, document_type_ids)) if document_type_ids else None
            ),
            "document_type__isnull": document_type_none,
            "storage_path__id": storage_path_id,
            "created__date__gt": created_after,
            "created__date__lt": created_before,
            "added__date__gt": added_after,
            "ordering": ordering,
            "page": page,
            "page_size": page_size,
        }
        data = await self._get_json("/api/documents/", **params)
        return Page[Document].model_validate(data)

    async def list_workflows(self) -> list[dict]:
        """Raw workflow objects — used to detect whether any workflow
        actually posts to our webhook (kept version-tolerant: shapes
        differ across paperless releases)."""
        data = await self._get_json("/api/workflows/", page_size=100)
        return list(data.get("results", []))

    async def get_document(self, doc_id: int) -> Document:
        return Document.model_validate(await self._get_json(f"/api/documents/{doc_id}/"))

    async def get_document_metadata(self, doc_id: int) -> dict[str, Any]:
        """Includes original checksum, archive info, and paperless's own
        page-level metadata."""
        return await self._get_json(f"/api/documents/{doc_id}/metadata/")

    async def download_original(self, doc_id: int) -> tuple[bytes, str]:
        """Returns (bytes, content_type) of the original file."""
        resp = await self._request(
            "GET", f"/api/documents/{doc_id}/download/", params={"original": "true"}
        )
        return resp.content, resp.headers.get("content-type", "application/octet-stream")

    async def update_document(self, doc_id: int, **fields: Any) -> Document:
        resp = await self._request("PATCH", f"/api/documents/{doc_id}/", json=fields)
        return Document.model_validate(resp.json())

    async def bulk_edit_documents(
        self, document_ids: list[int], method: str, parameters: dict[str, Any] | None = None
    ) -> Any:
        """POST /api/documents/bulk_edit/ — methods include set_correspondent,
        set_document_type, set_storage_path, add_tag, remove_tag, modify_tags,
        delete, merge, ..."""
        resp = await self._request(
            "POST",
            "/api/documents/bulk_edit/",
            json={
                "documents": document_ids,
                "method": method,
                "parameters": parameters or {},
            },
        )
        return resp.json()

    async def post_document(
        self,
        content: bytes,
        filename: str,
        *,
        title: str | None = None,
        correspondent_id: int | None = None,
        document_type_id: int | None = None,
        tag_ids: list[int] | None = None,
    ) -> str:
        """Upload a document for consumption. Returns the task UUID."""
        # NB: httpx interprets a non-dict `data` as raw content; form
        # fields must be a dict (repeated fields as list values).
        data: dict[str, Any] = {}
        if title:
            data["title"] = title
        if correspondent_id:
            data["correspondent"] = str(correspondent_id)
        if document_type_id:
            data["document_type"] = str(document_type_id)
        if tag_ids:
            data["tags"] = [str(t) for t in tag_ids]
        resp = await self._request(
            "POST",
            "/api/documents/post_document/",
            data=data,
            files={"document": (filename, content)},
        )
        return resp.json() if resp.headers.get("content-type", "").startswith(
            "application/json"
        ) else resp.text.strip().strip('"')

    async def get_task(self, task_uuid: str) -> dict[str, Any] | None:
        data = await self._get_json("/api/tasks/", task_id=task_uuid)
        if isinstance(data, list):
            return data[0] if data else None
        results = data.get("results", [])
        return results[0] if results else None

    # ----- taxonomy ---------------------------------------------------

    async def list_tags(self) -> list[Tag]:
        return await self._drain("/api/tags/", Tag)

    async def get_tag(self, tag_id: int) -> Tag:
        return Tag.model_validate(await self._get_json(f"/api/tags/{tag_id}/"))

    async def create_tag(self, **fields: Any) -> Tag:
        resp = await self._request("POST", "/api/tags/", json=fields)
        return Tag.model_validate(resp.json())

    async def update_tag(self, tag_id: int, **fields: Any) -> Tag:
        resp = await self._request("PATCH", f"/api/tags/{tag_id}/", json=fields)
        return Tag.model_validate(resp.json())

    async def delete_tag(self, tag_id: int) -> None:
        await self._request("DELETE", f"/api/tags/{tag_id}/")

    async def list_correspondents(self) -> list[Correspondent]:
        return await self._drain("/api/correspondents/", Correspondent)

    async def get_correspondent(self, cid: int) -> Correspondent:
        return Correspondent.model_validate(await self._get_json(f"/api/correspondents/{cid}/"))

    async def create_correspondent(self, **fields: Any) -> Correspondent:
        resp = await self._request("POST", "/api/correspondents/", json=fields)
        return Correspondent.model_validate(resp.json())

    async def update_correspondent(self, cid: int, **fields: Any) -> Correspondent:
        resp = await self._request("PATCH", f"/api/correspondents/{cid}/", json=fields)
        return Correspondent.model_validate(resp.json())

    async def delete_correspondent(self, cid: int) -> None:
        await self._request("DELETE", f"/api/correspondents/{cid}/")

    async def list_document_types(self) -> list[DocumentType]:
        return await self._drain("/api/document_types/", DocumentType)

    async def get_document_type(self, dtid: int) -> DocumentType:
        return DocumentType.model_validate(await self._get_json(f"/api/document_types/{dtid}/"))

    async def create_document_type(self, **fields: Any) -> DocumentType:
        resp = await self._request("POST", "/api/document_types/", json=fields)
        return DocumentType.model_validate(resp.json())

    async def update_document_type(self, dtid: int, **fields: Any) -> DocumentType:
        resp = await self._request("PATCH", f"/api/document_types/{dtid}/", json=fields)
        return DocumentType.model_validate(resp.json())

    async def delete_document_type(self, dtid: int) -> None:
        await self._request("DELETE", f"/api/document_types/{dtid}/")

    async def list_storage_paths(self) -> list[StoragePath]:
        return await self._drain("/api/storage_paths/", StoragePath)

    async def get_storage_path(self, spid: int) -> StoragePath:
        return StoragePath.model_validate(await self._get_json(f"/api/storage_paths/{spid}/"))

    async def create_storage_path(self, **fields: Any) -> StoragePath:
        resp = await self._request("POST", "/api/storage_paths/", json=fields)
        return StoragePath.model_validate(resp.json())

    async def update_storage_path(self, spid: int, **fields: Any) -> StoragePath:
        resp = await self._request("PATCH", f"/api/storage_paths/{spid}/", json=fields)
        return StoragePath.model_validate(resp.json())

    async def delete_storage_path(self, spid: int) -> None:
        await self._request("DELETE", f"/api/storage_paths/{spid}/")

    async def get_thumbnail(self, doc_id: int) -> tuple[bytes, str]:
        """(content, media_type) of the document thumbnail."""
        resp = await self._request("GET", f"/api/documents/{doc_id}/thumb/")
        return resp.content, resp.headers.get("content-type", "image/webp")

    async def list_custom_fields(self) -> list[CustomField]:
        return await self._drain("/api/custom_fields/", CustomField)

    # ----- misc -------------------------------------------------------

    async def ping(self) -> bool:
        try:
            await self._get_json("/api/documents/", page_size=1)
            return True
        except PaperlessError:
            return False
