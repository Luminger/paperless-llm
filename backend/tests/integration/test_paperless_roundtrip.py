"""Real-API integration tests against the ad-hoc paperless instance.

Run:
    podman compose -f deploy/test/compose.yaml up -d
    uv run pytest -m integration
"""

from __future__ import annotations

import pytest

from app.paperless import PaperlessClient

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(seeded, paperless_token):
    async with PaperlessClient(seeded, paperless_token) as c:
        yield c


async def test_seeded_taxonomy_present(client):
    tags = {t.name for t in await client.list_tags()}
    assert {"Rechnung", "invoice", "old-stuff-2019", "Inbox"} <= tags
    corrs = {c.name for c in await client.list_correspondents()}
    assert {"Kraxi", "Kraxi GmbH", "Bei Spiel GmbH"} <= corrs


async def test_inbox_documents_seeded(client):
    """A few documents represent fresh arrivals: tagged Inbox, with the
    tag flagged as a real paperless inbox tag (auto-applied to future
    consumptions)."""
    inbox = next(t for t in await client.list_tags() if t.name == "Inbox")
    assert inbox.is_inbox_tag is True
    page = await client.search_documents(tag_ids=[inbox.id], page_size=25)
    assert page.count >= 3
    titles = {d.title for d in page.results}
    assert any("en-invoice-scan-1958" in t for t in titles)
    assert any("ivy-1971" in t for t in titles)


async def test_fulltext_search_finds_consumed_document(client):
    # "Flugzeugallee" appears only in the Kraxi sample invoice content.
    page = await client.search_documents(query="Flugzeugallee")
    assert page.count >= 1
    assert any("Kraxi" in (d.title or "") for d in page.results)


async def test_field_filter_by_correspondent(client):
    corrs = {c.name: c.id for c in await client.list_correspondents()}
    page = await client.search_documents(correspondent_id=corrs["Kraxi"])
    assert page.count >= 1
    # The near-duplicate correspondent is a deliberate orphan.
    orphan = await client.search_documents(correspondent_id=corrs["Kraxi GmbH"])
    assert orphan.count == 0


async def test_document_content_patch_roundtrip(client):
    page = await client.search_documents(query="Flugzeugallee")
    doc = await client.get_document(page.results[0].id)
    original = doc.content
    try:
        updated = await client.update_document(doc.id, content=original + "\n[test-marker]")
        assert updated.content.endswith("[test-marker]")
    finally:
        await client.update_document(doc.id, content=original)


async def test_tag_crud_and_bulk_edit(client):
    tag = await client.create_tag(name="pllm-integration-tmp")
    try:
        page = await client.search_documents(query="weclapp")
        assert page.count >= 1
        doc_id = page.results[0].id
        await client.bulk_edit_documents(
            [doc_id], "modify_tags", {"add_tags": [tag.id], "remove_tags": []}
        )
        refreshed = await client.get_document(doc_id)
        assert tag.id in refreshed.tags
        await client.bulk_edit_documents(
            [doc_id], "modify_tags", {"add_tags": [], "remove_tags": [tag.id]}
        )
    finally:
        await client.delete_tag(tag.id)


async def test_download_original(client):
    page = await client.search_documents(title_contains="scan_0044")
    assert page.count >= 1
    data, content_type = await client.download_original(page.results[0].id)
    assert data[:5] == b"%PDF-" or "pdf" in content_type
