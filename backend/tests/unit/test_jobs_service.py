"""Job creation service: scope resolution, pagination, skip-active,
and the derived (session-computed) job counters.

These are the invariants bulk work stands on: a job must cover EVERY
matching document (not just page 1), must never double-book a document
that is already being analyzed, and its displayed progress must be
derived from the sessions — the one source that cannot go stale."""

from __future__ import annotations

import respx
from httpx import Response
from sqlalchemy import select

from app.db.models import (
    AgentKind,
    EntityType,
    Job,
    JobStatus,
    Session,
    SessionPhase,
    SessionStatus,
    Step,
    StepKind,
    StepState,
)
from app.services.jobs import (
    apply_live,
    create_job,
    live_job_counts,
    processed_document_ids,
    resolve_documents,
    resolve_next_batch,
)
from tests.conftest import PAPERLESS_URL


def _doc(doc_id: int, title: str | None = None) -> dict:
    return {"id": doc_id, "title": title or f"Doc {doc_id}", "content": "", "tags": []}


def _page(results: list[dict], count: int | None = None, all_ids: list[int] | None = None):
    body = {
        "count": count if count is not None else len(results),
        "next": None,
        "previous": None,
        "results": results,
    }
    if all_ids is not None:
        body["all"] = all_ids
    return Response(200, json=body)


# ----- resolve_documents / _all_matching ------------------------------


async def test_resolve_documents_explicit_ids_dedupe_without_paperless(paperless_client):
    """Explicit ids resolve locally (deduped, order kept) — no paperless
    round-trip, so a job over selected documents works even when the
    listing API is slow or down."""
    with respx.mock:  # no routes mocked: any HTTP call would error
        ids, titles = await resolve_documents(paperless_client, document_ids=[3, 3, 5, 3])
    assert ids == [3, 5]
    assert titles == {}


@respx.mock
async def test_resolve_documents_inbox_without_inbox_tag_is_empty(paperless_client):
    """No inbox tag configured in paperless: the inbox scope must resolve
    to nothing instead of falling through to some broader listing."""
    respx.get(f"{PAPERLESS_URL}/api/tags/").mock(
        return_value=Response(
            200,
            json={"count": 1, "next": None, "results": [
                {"id": 2, "name": "steuer", "is_inbox_tag": False,
                 "match": "", "matching_algorithm": 0},
            ]},
        )
    )
    ids, titles = await resolve_documents(paperless_client, inbox=True)
    assert ids == [] and titles == {}


@respx.mock
async def test_all_matching_walks_every_page(paperless_client):
    """AUDIT API-F3 regression: a filtered scope larger than one page must
    include the documents beyond page 1 — otherwise bulk jobs silently
    cap at 100 documents."""
    page1 = [_doc(i) for i in range(1, 101)]
    page2 = [_doc(i) for i in range(101, 151)]

    def respond(request):
        page = request.url.params.get("page", "1")
        return _page(page1 if page == "1" else page2, count=150)

    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(side_effect=respond)
    ids, titles = await resolve_documents(paperless_client, tag_id=9)
    assert ids == list(range(1, 151))
    assert titles[150] == "Doc 150"


@respx.mock
async def test_all_matching_stops_on_empty_page(paperless_client):
    """Documents deleted mid-iteration shrink the result set below
    `count`: an empty page must terminate the walk, not 404-loop."""
    calls: list[str] = []

    def respond(request):
        calls.append(request.url.params.get("page", "1"))
        if request.url.params.get("page", "1") == "1":
            return _page([_doc(i) for i in range(1, 101)], count=500)
        return _page([], count=500)

    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(side_effect=respond)
    ids, _ = await resolve_documents(paperless_client, tag_id=9)
    assert ids == list(range(1, 101))
    assert calls == ["1", "2"]  # bounded: gave up at the first empty page


@respx.mock
async def test_all_matching_prefers_server_all_when_larger(paperless_client):
    """When paperless supplies `all` with MORE ids than the paged walk
    collected, the server view is authoritative (it sees concurrent
    additions the walk missed)."""
    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(
        return_value=_page([_doc(1), _doc(2)], count=2, all_ids=[1, 2, 3, 4])
    )
    ids, _ = await resolve_documents(paperless_client, tag_id=9)
    assert ids == [1, 2, 3, 4]


# ----- processed_document_ids / resolve_next_batch --------------------


async def _add_session(db, *, entity_id: int, phase, status=SessionStatus.idle,
                       params: dict | None = None, entity_type=EntityType.document,
                       job_id: int | None = None) -> Session:
    s = Session(
        agent_kind=AgentKind.document,
        entity_type=entity_type,
        entity_id=entity_id,
        phase=phase,
        status=status,
        params=params or {},
        job_id=job_id,
    )
    db.add(s)
    await db.flush()
    return s


async def test_processed_ids_count_only_completed_metadata_analyses(db):
    """"Processed" for corpus curation = a DONE metadata analysis. An
    OCR-only pass fixes text, not metadata — it must not mark the
    document as curated; unfinished sessions never count."""
    await _add_session(db, entity_id=1, phase=SessionPhase.done)
    await _add_session(db, entity_id=2, phase=SessionPhase.done, params={"ocr_only": True})
    await _add_session(db, entity_id=3, phase=SessionPhase.queued)
    await _add_session(db, entity_id=9, phase=SessionPhase.done, entity_type=EntityType.tag)
    await db.commit()
    assert await processed_document_ids(db) == {1}


@respx.mock
async def test_resolve_next_batch_skips_done_and_crosses_pages(db, paperless_client):
    """The corpus batch button: oldest-first, never re-picks an analyzed
    document, and keeps paginating until the batch is full — pressing the
    same button repeatedly walks the whole archive exactly once."""
    for done_id in range(1, 100):  # 1..99 already analyzed
        await _add_session(db, entity_id=done_id, phase=SessionPhase.done)
    await db.commit()

    def respond(request):
        assert request.url.params["ordering"] == "created"  # deterministic order
        if request.url.params.get("page", "1") == "1":
            return _page([_doc(i) for i in range(1, 101)], count=103)
        return _page([_doc(i) for i in range(101, 104)], count=103)

    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(side_effect=respond)
    assert await resolve_next_batch(db, paperless_client, 3) == [100, 101, 102]


@respx.mock
async def test_resolve_next_batch_exhausted_corpus_is_empty(db, paperless_client):
    await _add_session(db, entity_id=1, phase=SessionPhase.done)
    await db.commit()
    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(
        return_value=_page([_doc(1)], count=1)
    )
    assert await resolve_next_batch(db, paperless_client, 5) == []


# ----- create_job ------------------------------------------------------


@respx.mock
async def test_create_job_skips_active_but_not_failed_sessions(db, paperless_client):
    """A document already mid-analysis must not be double-booked, but a
    FAILED run doesn't block a fresh one — retrying via a new job is the
    recovery path."""
    await _add_session(db, entity_id=7, phase=SessionPhase.analyzing,
                       status=SessionStatus.running)
    await _add_session(db, entity_id=8, phase=SessionPhase.queued,
                       status=SessionStatus.failed)
    await db.commit()
    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(
        return_value=_page([_doc(7), _doc(8), _doc(9)])
    )

    job, ids = await create_job(db, paperless_client, document_ids=[7, 8, 9])
    assert ids == [8, 9]  # 7 active -> skipped; 8 failed -> retryable
    assert job.params["skipped_active"] == [7]
    assert job.total == 2
    sessions = (
        await db.scalars(select(Session).where(Session.job_id == job.id))
    ).all()
    assert sorted(s.entity_id for s in sessions) == [8, 9]


@respx.mock
async def test_create_job_single_document_label_uses_title(db, paperless_client):
    """Users see names, not ids: a one-document job is labeled with the
    document's title, resolved via the batched id__in lookup."""
    route = respx.get(f"{PAPERLESS_URL}/api/documents/").mock(
        return_value=_page([_doc(7, title="Telarko Rechnung")])
    )
    job, _ = await create_job(db, paperless_client, document_ids=[7])
    assert job.params["label"] == "Telarko Rechnung"
    assert route.calls.last.request.url.params["id__in"] == "7"


@respx.mock
async def test_create_job_label_survives_title_lookup_failure(db, paperless_client):
    """Labels are cosmetic: a broken title lookup must never fail the
    job — it falls back to the count-based label."""
    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(return_value=Response(500))
    job, ids = await create_job(db, paperless_client, document_ids=[7, 8])
    assert ids == [7, 8]
    assert job.params["label"] == "2 selected documents"


@respx.mock
async def test_create_job_ocr_only_becomes_bulk_ocr(db, paperless_client):
    """ocr_only is the corpus-rehab job: it forces the bulk_ocr kind and
    a redo-OCR step whose input marks the pipeline as ENDING at the gate."""
    respx.get(f"{PAPERLESS_URL}/api/documents/").mock(return_value=_page([_doc(7)]))
    job, ids = await create_job(
        db, paperless_client, document_ids=[7], ocr_only=True,
        instructions="mind the stamps",
    )
    assert job.kind == "bulk_ocr"
    assert job.params["redo_ocr"] is True and job.params["ocr_only"] is True
    step = await db.scalar(select(Step))
    assert step.kind == StepKind.ocr
    assert step.input == {"ocr_only": True, "instructions": "mind the stamps"}
    session = await db.scalar(select(Session).where(Session.job_id == job.id))
    assert session.params["ocr_only"] is True
    assert session.title == "OCR pass"


# ----- live_job_counts / apply_live ------------------------------------


class _JobView:
    """Minimal JobOut-shaped object for apply_live."""

    def __init__(self, status: JobStatus = JobStatus.running):
        self.status = status
        self.done = self.failed = self.stopped = 0


async def test_live_job_counts_derive_from_sessions(db):
    """AUDIT SV-M1: progress is computed FROM the sessions. A failed
    session with a retry already queued is UNFINISHED (it will run
    again), not failed; stopped sessions are neither done nor pending."""
    job = Job(kind="bulk_analyze", total=5)
    db.add(job)
    await db.flush()
    await _add_session(db, entity_id=1, phase=SessionPhase.done, job_id=job.id)
    await _add_session(db, entity_id=2, phase=SessionPhase.analyzing,
                       status=SessionStatus.failed, job_id=job.id)
    retrying = await _add_session(db, entity_id=3, phase=SessionPhase.analyzing,
                                  status=SessionStatus.failed, job_id=job.id)
    db.add(Step(session_id=retrying.id, kind=StepKind.analysis, state=StepState.pending))
    await _add_session(db, entity_id=4, phase=SessionPhase.stopped, job_id=job.id)
    await _add_session(db, entity_id=5, phase=SessionPhase.queued, job_id=job.id)
    await db.commit()

    counts = await live_job_counts(db, [job.id])
    done, failed, stopped, unfinished = counts[job.id]
    assert (done, failed, stopped) == (1, 1, 1)
    assert unfinished == 2  # the queued one AND the retrying-failed one


async def test_live_job_counts_empty_input_short_circuits(db):
    assert await live_job_counts(db, []) == {}


def test_apply_live_settles_and_respects_sticky_statuses():
    """Derived job status: running while anything is unfinished; settles
    by what actually finished; cancelled/paused are sticky and never
    overwritten by the derivation."""
    assert apply_live(_JobView(), (1, 0, 0, 2)).status == JobStatus.running
    assert apply_live(_JobView(), (3, 1, 0, 0)).status == JobStatus.completed
    assert apply_live(_JobView(), (0, 2, 0, 0)).status == JobStatus.failed
    # All remaining sessions stopped, one done -> completed, not running.
    assert apply_live(_JobView(), (1, 0, 2, 0)).status == JobStatus.completed
    assert apply_live(_JobView(JobStatus.cancelled), (1, 0, 0, 2)).status == JobStatus.cancelled
    assert apply_live(_JobView(JobStatus.paused), (1, 0, 0, 2)).status == JobStatus.paused
    view = apply_live(_JobView(), (2, 1, 1, 0))
    assert (view.done, view.failed, view.stopped) == (2, 1, 1)
