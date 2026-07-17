"""Seed corpus for the ad-hoc paperless-ngx instance.

A first-class asset (DESIGN.md "Testing strategy"). The corpus consists
EXCLUSIVELY of real, publicly shared, redistributable PDFs (public
domain or Apache-2.0 sample documents — provenance and licenses in
``tests/fixtures/corpus/external/MANIFEST.md``): real invoices, official
letters, table-heavy forms, and raw scans without a text layer, in
German and English. Nothing is generated.

On top of the files, the seeder creates a deliberately messy taxonomy —
near-duplicate correspondents/tags/types, bad casing, orphans, wrong or
missing assignments, junk titles — which is what the agents are for.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from app.paperless import PaperlessClient, PaperlessError

CORPUS_DIR = Path(__file__).resolve().parent.parent / "tests/fixtures/corpus/external"


@dataclass
class SeedDoc:
    filename: str  # file in CORPUS_DIR
    title: str | None = None  # None -> paperless uses the filename stem
    tags: list[str] = field(default_factory=list)
    correspondent: str | None = None
    document_type: str | None = None


# Deliberately messy taxonomy. Near-duplicates and bad casing are
# intentional — this is the cleanup material for the taxonomy agents.
SEED_CORRESPONDENTS = [
    "Kraxi",
    "Kraxi GmbH",  # near-duplicate of the above (orphan)
    "Bei Spiel GmbH",  # real sender of the RE-20170509/505 invoice; unassigned
    "weclapp",
    "Bundesministerium der Finanzen",
    "Federal Reserve Board",
    "internal revenue service",  # bad casing
    "Unbekannt",  # junk drawer
]
SEED_TAGS = ["Rechnung", "invoice", "wichtig", "steuer", "old-stuff-2019", "scan"]
SEED_DOCUMENT_TYPES = ["Rechnung", "Invoice", "Brief"]

# Fresh arrivals: carry the Inbox tag, like real unreviewed documents.
# The Inbox tag (is_inbox_tag=true) is created only after the corpus is
# consumed, so the rest of the archive looks curated — while every
# document uploaded later is auto-inboxed by paperless itself.
INBOX_TAG = "Inbox"
INBOX_TITLES = [
    "en-invoice-scan-1958",  # raw 1958 typewriter scan, no text layer
    "de-invoice-zugferd-teilrechnung",  # e-invoice, no metadata yet
    "en-invoice-ivy-1971",  # raw 1971 typewriter scan
]

SEED_DOCUMENTS: list[SeedDoc] = [
    # --- curated (correctly or partially maintained) ------------------
    SeedDoc(
        filename="de-invoice-kraxi.pdf",
        title="Kraxi Rechnung 2014-03",
        tags=["Rechnung"],
        correspondent="Kraxi",  # while "Kraxi GmbH" sits unused next to it
        document_type="Rechnung",
    ),
    SeedDoc(
        filename="de-invoice-weclapp-re1001.pdf",
        title="Rechnung RE1001",
        tags=["invoice"],  # the English near-duplicate tag on a German doc
        correspondent="weclapp",
        document_type="Invoice",  # the duplicate type
    ),
    SeedDoc(
        filename="de-letter-bmf-pauschbetraege-2024.pdf",
        title="BMF Pauschbetraege 2024",
        tags=["steuer"],
        correspondent="Bundesministerium der Finanzen",
        document_type="Brief",
    ),
    SeedDoc(
        filename="en-letter-frb-sr2404.pdf",
        title="FRB SR 24-4",
        tags=["wichtig"],
        correspondent="Federal Reserve Board",
        document_type="Brief",
    ),
    SeedDoc(
        filename="en-form-irs-f1040.pdf",
        title="Form 1040",
        tags=["steuer"],
        correspondent="internal revenue service",
    ),
    # --- badly maintained (the DocumentAgent's material) --------------
    SeedDoc(
        # Real invoice from "Bei Spiel GmbH" (RE-20170509/505) hiding
        # behind a scanner title, no correspondent/type assigned.
        filename="de-invoice-zugferd-mustang.pdf",
        title="scan_0001",
        tags=["scan"],
    ),
    SeedDoc(
        filename="de-invoice-datev-belegverfilmung.pdf",
        title="scan_0044",
        tags=["scan"],
        correspondent="Unbekannt",
    ),
    SeedDoc(
        filename="en-invoice-scan-1956.pdf",
        title="scan_0234",
        tags=["scan", "old-stuff-2019"],
        correspondent="Unbekannt",
    ),
    SeedDoc(
        filename="en-letter-cia-duncan.pdf",
        tags=["old-stuff-2019"],
    ),
    # --- untouched / fresh arrivals (no metadata at all) --------------
    SeedDoc(filename="de-invoice-zugferd-einfach.pdf"),
    SeedDoc(filename="de-invoice-zugferd-teilrechnung.pdf"),
    SeedDoc(filename="en-invoice-scan-1958.pdf"),
    SeedDoc(filename="en-invoice-ivy-1971.pdf"),
]


async def seed_corpus(
    base_url: str,
    token: str = "",
    *,
    username: str = "",
    password: str = "",
    wait: bool = False,
) -> str:
    """Create the taxonomy and upload the corpus. Idempotent-ish: entities
    are looked up by name first; re-uploaded documents are rejected by
    paperless via checksum dedup."""
    async with PaperlessClient(
        base_url, token, username=username, password=password
    ) as client:
        tag_ids: dict[str, int] = {t.name: t.id for t in await client.list_tags()}
        for name in SEED_TAGS:
            if name not in tag_ids:
                tag_ids[name] = (await client.create_tag(name=name)).id

        corr_ids: dict[str, int] = {
            c.name: c.id for c in await client.list_correspondents()
        }
        for name in SEED_CORRESPONDENTS:
            if name not in corr_ids:
                corr_ids[name] = (await client.create_correspondent(name=name)).id

        type_ids: dict[str, int] = {
            d.name: d.id for d in await client.list_document_types()
        }
        for name in SEED_DOCUMENT_TYPES:
            if name not in type_ids:
                type_ids[name] = (await client.create_document_type(name=name)).id

        tasks: list[str] = []
        skipped = 0
        for doc in SEED_DOCUMENTS:
            path = CORPUS_DIR / doc.filename
            try:
                task = await client.post_document(
                    path.read_bytes(),
                    doc.filename,
                    title=doc.title,
                    correspondent_id=corr_ids.get(doc.correspondent)
                    if doc.correspondent
                    else None,
                    document_type_id=type_ids.get(doc.document_type)
                    if doc.document_type
                    else None,
                    tag_ids=[tag_ids[t] for t in doc.tags],
                )
                tasks.append(task)
            except PaperlessError as e:
                # Duplicate checksum on re-seed -> 400/409; anything else is real.
                if e.status_code in (400, 409):
                    skipped += 1
                else:
                    raise

        if wait and tasks:
            deadline = asyncio.get_event_loop().time() + 300
            pending = set(tasks)
            while pending and asyncio.get_event_loop().time() < deadline:
                for t in list(pending):
                    info = await client.get_task(t)
                    if info and info.get("status") in ("SUCCESS", "FAILURE"):
                        pending.discard(t)
                if pending:
                    await asyncio.sleep(2)

        # Inbox: create the inbox tag AFTER consumption (so only the
        # designated fresh-arrival docs carry it), then assign it.
        inbox_note = ""
        if INBOX_TAG not in tag_ids:
            tag_ids[INBOX_TAG] = (
                await client.create_tag(name=INBOX_TAG, is_inbox_tag=True, color="#e11d48")
            ).id
        if wait:
            inbox_doc_ids: list[int] = []
            for title in INBOX_TITLES:
                page = await client.search_documents(title_contains=title, page_size=5)
                inbox_doc_ids += [d.id for d in page.results]
            if inbox_doc_ids:
                await client.bulk_edit_documents(
                    inbox_doc_ids,
                    "modify_tags",
                    {"add_tags": [tag_ids[INBOX_TAG]], "remove_tags": []},
                )
            inbox_note = f", {len(inbox_doc_ids)} documents in Inbox"
        else:
            inbox_note = ", Inbox tag created (assignment needs --wait)"

        return (
            f"seeded: {len(SEED_TAGS)} tags, {len(SEED_CORRESPONDENTS)} correspondents, "
            f"{len(SEED_DOCUMENT_TYPES)} types, {len(tasks)} documents uploaded"
            + (f", {skipped} skipped (already present)" if skipped else "")
            + inbox_note
        )
