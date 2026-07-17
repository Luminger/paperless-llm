"""Live-model scenarios: real local LLM + ad-hoc paperless.

The automated ground truth for "does Qwen3.6-27b (or whatever is
configured) actually perform". Assertions are deliberately loose:
proposal kinds and target entities, not exact wording.

Run (with deploy/test compose up and real endpoints configured):
    uv run pytest -m live_llm -x -s
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.agents.runner import run_agent_turn
from app.db.models import AgentKind, EntityType, Proposal, Session
from app.paperless import PaperlessClient

pytestmark = [pytest.mark.live_llm, pytest.mark.integration]


@pytest.fixture
async def client(seeded, paperless_token):
    async with PaperlessClient(seeded, paperless_token) as c:
        yield c


@pytest.fixture
async def live_db(tmp_path, monkeypatch):
    """File-backed DB so results can be inspected after a run."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/live.sqlite3")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_document_agent_on_mislabeled_invoice(client, live_db):
    """The real 'Bei Spiel GmbH' invoice (RE-20170509/505) is seeded with
    title 'scan_0001', no correspondent and no type. The agent should at
    minimum propose a better title; ideally it assigns the existing
    'Bei Spiel GmbH' correspondent."""
    page = await client.search_documents(title_contains="scan_0001")
    assert page.count >= 1, "seed document missing"
    doc_id = page.results[0].id

    session = Session(
        agent_kind=AgentKind.document,
        entity_type=EntityType.document,
        entity_id=doc_id,
    )
    live_db.add(session)
    await live_db.commit()

    outcome = await run_agent_turn(
        client, live_db, session, f"Process document id={doc_id}."
    )
    print(f"\n--- agent output ---\n{outcome.output}\n")

    proposals = (
        await live_db.scalars(select(Proposal).where(Proposal.session_id == session.id))
    ).all()
    assert proposals, "agent proposed nothing for an obviously mislabeled document"

    metadata_props = [p for p in proposals if p.kind == "update_document_metadata"]
    assert metadata_props, "expected an update_document_metadata proposal"
    payload = metadata_props[-1].agent_payload
    assert payload.get("title") and payload["title"] != "scan_0001", (
        f"expected a better title, got: {payload}"
    )


async def test_ocr_pipeline_on_real_typewriter_scan(client, live_db):
    """The 1958 declassified invoice scan has no text layer; paperless's
    tesseract output for it is noisy. Vision OCR should read the key
    fields and diverge measurably from the tesseract content."""
    from app.llm.ocr import run_ocr

    page = await client.search_documents(title_contains="en-invoice-scan-1958")
    assert page.count >= 1, "external corpus scan missing"
    outcome = await run_ocr(client, live_db, page.results[0].id)
    print(f"\n--- scan OCR (sim vs tesseract={outcome.similarity}) ---")
    print(outcome.text[:600])
    assert len(outcome.text) > 200, "vision OCR produced almost nothing"
    assert "invoice" in outcome.text.lower() or "inv" in outcome.text.lower()


async def test_ocr_pipeline_reads_seeded_pdf(client, live_db):
    """Vision OCR of a born-digital seed PDF (the Kraxi sample invoice)
    should closely match the tesseract content paperless produced."""
    from app.llm.ocr import run_ocr

    page = await client.search_documents(query="Flugzeugallee")
    assert page.count >= 1
    outcome = await run_ocr(client, live_db, page.results[0].id)
    print(f"\n--- OCR ({len(outcome.pages)} page(s), sim={outcome.similarity}) ---")
    print(outcome.text[:500])
    assert "Kraxi" in outcome.text
    assert "1.005,55" in outcome.text, "gross total must be transcribed exactly"
    assert outcome.similarity is None or outcome.similarity > 0.5
