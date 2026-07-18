"""Job scope resolution against a respx-mocked paperless."""

from __future__ import annotations

import respx
from httpx import Response

from tests.conftest import PAPERLESS_URL


@respx.mock
async def test_resolve_documents_paginates_beyond_100(paperless_client):
    """AUDIT API-F3: scope resolution must never silently cap at one
    page — a 250-document archive yields 250 ids even without
    `Page.all`."""
    from app.services.jobs import resolve_documents

    def page_json(page_no: int):
        start = (page_no - 1) * 100
        n = min(100, 250 - start)
        return {
            "count": 250,
            "next": "x" if start + n < 250 else None,
            "results": [
                {"id": start + i + 1, "title": f"doc {start + i + 1}",
                 "content": "", "tags": [], "correspondent": None,
                 "document_type": None, "storage_path": None,
                 "created": "2024-01-01", "custom_fields": []}
                for i in range(n)
            ],
        }

    def responder(request):
        import urllib.parse

        q = urllib.parse.parse_qs(urllib.parse.urlparse(str(request.url)).query)
        return Response(200, json=page_json(int(q.get("page", ["1"])[0])))

    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(side_effect=responder)

    ids, titles = await resolve_documents(paperless_client, all_documents=True)
    assert len(ids) == 250
    assert titles[250] == "doc 250"
