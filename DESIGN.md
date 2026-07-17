# paperless-llm — Design

LLM-assisted metadata pipeline and taxonomy governance for
[paperless-ngx](https://docs.paperless-ngx.com/), built to run **exclusively
against self-hosted models**. Documents and metadata never leave the local
network.

## Goals

1. **Document processing**: (re-)OCR documents with a local vision LLM,
   compare against paperless's existing OCR, and propose corrected content
   plus metadata (title, tags, correspondent, document type, dates, storage
   path, custom fields).
2. **Taxonomy governance**: review, merge, rename, and clean up tags,
   correspondents, and document types — one entity at a time, with the LLM
   adjudicating and a human approving.
3. **Human-in-the-loop by default**: agents emit *proposals*, not writes.
   Every proposal is reviewable, editable, steerable via chat, and journaled
   when applied. Auto-apply is opt-in per job.
4. **Privacy by construction**: exactly one class of LLM endpoint (local,
   OpenAI-compatible) exists in the codebase. There are no cloud-provider
   code paths. The tool is *built* with cloud models; it *runs* only on
   local ones.

## Non-goals (v1)

- Regenerating searchable PDFs / archive files. LLM OCR yields no bounding
  boxes, so no invisible text layer is possible. We update paperless's
  `content` field only.
- Protecting re-OCR'd content from being overwritten by a later paperless
  reprocess (tracked as a future concern; the journal at least detects it).
- Multi-user auth. Deployment sits behind a reverse proxy / VPN. A simple
  shared token may be added for the webhook endpoint.
- Token-level streaming of agent output (blocked on qwen3_xml streaming
  parser bugs upstream; see Model profiles).

## System context

```
paperless-ngx  <── REST ──  backend (FastAPI)  ── REST/SSE ──>  frontend (React SPA)
                             │        │
                             │        └── in-process async workers over a
                             │            persistent DB queue (two lanes)
                             │
              OpenAI-compatible endpoints (all local):
                ├── llm.agent       e.g. vLLM / Qwen3.6-27B on ares:8001
                ├── llm.ocr         defaults to llm.agent; may be a dedicated OCR model
                ├── llm.embeddings  optional; e.g. TEI / Qwen3-Embedding-0.6B (enables RAG)
                └── llm.reranker    optional; Cohere-compatible /v1/rerank
```

App state lives in **SQLite or PostgreSQL** (both supported via SQLAlchemy;
vectors via `sqlite-vec` or `pgvector` respectively).

## Model profiles (portability layer)

Every quirk of a given serving setup is configuration, not code. Reference
setup and rationale (vLLM v0.25.x, Qwen3.6-27B INT4 on 2x3090):

| Concern | Config knob | Reference value / rationale |
|---|---|---|
| Endpoint & model | `base_url`, `model`, `api_key` | `http://127.0.0.1:8001/v1`, `qwen3.6-27b` |
| Server-side concurrency | `max_concurrent` (app-level semaphore, shared across workers and interactive lane) | 2 — server has `--max-num-seqs 3` shared with other consumers |
| Images per request | `llm.ocr.max_images_per_request` | 2 — server has `--limit-mm-per-prompt {"image": 2}` |
| Token streaming | `supports_streaming` | false — qwen3_xml streaming parser has known edge-case bugs (vLLM PRs #43714/#43783); UI uses event-level SSE instead |
| Thinking mode | `thinking` = `server_default` \| `on` \| `off` (via `chat_template_kwargs`) | `server_default` (on); reasoning shown collapsed in UI |
| Sampling | optional per-profile overrides (`temperature`, `top_p`, ...) | agent: server defaults; OCR: `temperature=0.1` |
| Context budget | `max_input_tokens` (used to clamp tool results / doc content) | 262144 native; clamp far below for sanity |

Profiles: `llm.agent` (tool-calling chat), `llm.ocr` (vision; falls back to
`llm.agent` when unset), `llm.embeddings` (optional; enables RAG features),
`llm.reranker` (optional; improves RAG ranking, degrades gracefully).

Config via TOML file + environment overrides (pydantic-settings). Paperless
connection: `base_url` + API token.

## Agent framework

**pydantic-ai.** Typed tools, typed/validated outputs, `BinaryContent` for
local images, first-class OpenAI-compatible `base_url`, serializable message
histories (needed for persisted, resumable sessions). DSPy was considered
and rejected as the primary abstraction; it may return later as an optional
prompt-optimization layer once reviewed proposals accumulate as training
examples.

### Agents

All agents operate **one entity at a time** with capped tool iterations.

| Agent | Bound to | Purpose |
|---|---|---|
| `DocumentAgent` | document | Full-entry processing: OCR compare, metadata proposals across all fields |
| `TagAgent` | tag | Review one tag: rename, merge into another, split, delete, fix matching rule |
| `CorrespondentAgent` | correspondent | Same shape as TagAgent, correspondent-flavored (incl. merge = reassign docs + delete) |
| `DocumentTypeAgent` | document type | Same shape as TagAgent |

### Shared toolset

One paperless client module (`httpx`, token auth); tools are thin typed
wrappers, shared across agents:

- `search_documents` — full-text (`?query=`) **and** field filters
  (correspondent, type, tags, dates, title, ASN)
- `get_document` / `get_document_content` (clamped to context budget)
- `semantic_search_documents` (RAG; only registered when embeddings
  configured)
- `find_similar_entities` (RAG entity matching over tag/correspondent/doctype
  names + descriptions — prevents near-duplicate creation)
- `list_tags` / `list_correspondents` / `list_document_types` (+ doc counts,
  matching rules)
- `ocr_document(doc_id)` — runs the OCR pipeline (see below), returns text +
  similarity metrics vs. existing content
- `propose_*` tools — the **only** way agents effect change: create typed
  draft proposals as side effects (see Proposals)

Matching rules (`match`, `matching_algorithm`) are first-class: taxonomy
proposals include rule updates, since paperless's own auto-assignment
depends on them.

### OCR pipeline (not an agent)

Plain vision calls outside any tool loop — sidesteps the unverified
"images + tool calls in one request" combination and keeps agent contexts
small:

1. Fetch original file from paperless; render pages to images (PyMuPDF),
   configurable DPI.
2. Per request: ≤ `max_images_per_request` pages → markdown text. Low
   temperature. Language-agnostic prompt.
3. Concatenate; compute similarity vs. existing `content` (normalized
   Levenshtein / token-set ratio) to flag "paperless OCR is bad here".
4. Cache results keyed by (doc id, file checksum, model, prompt version).

## RAG subsystem (optional feature)

Enabled when `llm.embeddings` is configured.

- **Index 1 — document chunks**: existing paperless `content`, chunked,
  embedded, stored with doc metadata for filtered kNN.
- **Index 2 — entities**: one embedding per tag/correspondent/doctype
  (name + description + matching rule).
- **Sync**: periodic incremental indexer polling paperless `modified`
  timestamps; full reindex job available from the UI.
- **Reranker**: optional second stage over kNN candidates
  (Cohere-compatible `/v1/rerank`); skipped when unconfigured.
- **Store**: `VectorStore` interface (`upsert`, `delete`, `knn_search` with
  metadata filter) with two backends: `sqlite-vec` (SQLite) and `pgvector`
  (PostgreSQL). No third storage system.

Also used outside agent loops: taxonomy review jobs shortlist merge
candidates via embedding similarity + string distance (cheap, deterministic),
so the LLM adjudicates rather than explores.

## Document pipeline & the OCR gate

Document analyses run as a phased pipeline per session:

```
queued ──(redo_ocr)──> ocr_running ──> ocr_review  (GATE: user)
   │                                      │ accept / fix by hand / keep existing
   └──────────────> analyzing <───────────┘
                        │
                      done
```

The user chooses per analysis whether to re-OCR. If so, the pipeline
STOPS at a review gate: an editable diff (side-by-side or unified,
rendered by react-diff-viewer-continued) of current paperless content
vs. the vision-model OCR. The user may fix the text by hand; the
accepted text is written to paperless via an internal, journaled
ReplaceContent proposal (agent payload = raw OCR, user payload = the
hand-fix). Only then does the metadata agent run — against the
post-gate content. Agents never re-OCR or rewrite content themselves;
similarity scores are internal-only and never user-facing.

Stages are queue-agnostic functions; they run as referenced asyncio
background tasks today and move onto the celery lanes in M4 unchanged.
Startup recovery marks orphaned in-flight sessions as failed.

## Sessions, proposals, steering

The core unit is the **Session** — one conversation with one agent,
optionally bound to an entity. The pydantic-ai message history is persisted
(serialized) so any session can be resumed and steered.

```
Session        id, agent_kind, entity_type?, entity_id?, title,
               message_history (JSON), status, created/updated
Proposal       id, session_id, kind, revision, supersedes_id?,
               agent_payload   (immutable: exactly what the model emitted)
               user_payload?   (user's edited version, full edit visibility)
               status: draft | pending | approved | rejected | applied | superseded
AppliedChange  proposal_id, paperless_before (snapshot), paperless_after,
               applied_at    — the undo journal
Job            kind, params (apply_policy: review|auto, batch_size, filters,
               schedule?), progress, status
```

**Steering** (`POST /sessions/{id}/messages`): appends a user message and
re-runs the agent on the stored history. The agent may answer in plain text,
emit a revised proposal (new revision superseding the old — chain preserved
and fully visible), or both. If the user hand-edited fields first, the
current `user_payload` is injected into context ("the user amended your
proposal as follows: ...") so manual fixes and "agent, fix it" compose.

**Apply engine**: applies `user_payload` if present, else the latest
revision's `agent_payload`. Snapshots before-state to the journal. Merges
(correspondents/tags) are implemented as bulk reassignment
(`/api/documents/bulk_edit/`) followed by entity deletion, journaled for
undo. Per-job `auto` policy applies without review.

Proposal kinds (v1): `update_document_metadata`, `replace_content`,
`create_entity`, `update_entity`, `merge_entities`, `delete_entity`
(entity ∈ {tag, correspondent, document_type, storage_path}).

## Queueing & triggers

**The Step is the unit of everything.** A session is an ordered list
of `steps` rows — one per executable timeline element (ocr, analysis,
chat). The step doubles as the queue item: in-process async workers
claim pending steps by lane — deliberately NOT celery/redis: this is a
single-node tool whose true concurrency cap is the LLM endpoint
itself, the SSE event bus is in-process, and a DB queue gives strictly
better restart behavior (queued work survives; interrupted work is
retried). Executors are a registry (kind -> coroutine) that fill
`step.result`, return AWAIT_USER (gates), or raise; ALL state
transitions, attempt history, retry policy, and the session's derived
phase/status live in the engine (single writer). Generic actions apply
to every kind, implemented once: retry, redo (supersede + fresh step
with amended input — an OCR re-run is a redo), resolve (awaiting_user).
A distributed queue remains a contained swap if multi-node ever
becomes real. Two lanes:

- `interactive` — chat turns, single manual analyses; own worker slots
  so bulk jobs never starve the UI.
- `batch` — campaigns, webhook-ingested docs, (M5) RAG indexing.

**Failure policy**: a failed step is retried `queue.retry_attempts`
times with `queue.retry_delay_seconds` between attempts; every attempt
is appended to the step's attempt log (never shadowed) and shown in
the timeline. "Retry now" skips the backoff or revives exhausted
steps — manual retries are never limited. Worker concurrency per lane
and the per-endpoint `max_concurrent` semaphore are settings.

**Triggers**:
1. Manual — per entity from the UI.
2. Bulk — UI multi-select / filter → batch job (initial-review campaigns).
3. Webhook — `POST /api/webhooks/paperless` for paperless-ngx workflow
   actions on document-added (shared-secret header).

## HTTP API surface (backend)

```
/api/sessions            CRUD, POST /{id}/messages (steering), GET /{id}/events (SSE)
/api/proposals           list/filter, PATCH /{id} (user edits), POST /{id}/approve|reject|apply
/api/jobs                CRUD, progress, cancel
/api/entities            proxied browse: documents/tags/correspondents/doctypes (+ queue-for-analysis)
/api/webhooks/paperless  ingress
/api/settings            model profiles, queue config, RAG index status/reindex
```

SSE event stream carries: agent run started, tool call started/finished,
proposal drafted/revised, text output (chunked at event granularity, not
token granularity), run finished, job progress.

## Frontend

**React + Vite + TypeScript + Tailwind + shadcn/ui**, npm, TanStack Query,
SSE for live updates. No SSR; FastAPI serves the built `dist/` in
production, Vite dev-server proxy in development.

**The session timeline is the single review surface.** There is no
separate review queue: every analysis is a session, and everything it
produces — the OCR gate, the agent's run, and ALL proposals (metadata
updates and entity creations alike, rendered as full inline editor
cards with resolved names) — lives on one timeline page.
`/proposals/:id` deep-links redirect to the owning session.

Views:
1. **Analyses** (home) — all sessions with phase/status badges
   ("OCR review needed", "no changes proposed", failures with errors)
2. **Session timeline** (centerpiece) — request params · OCR gate
   (editable diff) · analysis step · inline proposal cards · (M2) chat
   steering appended as further timeline steps
3. **Documents** — search/browse, per-document analyze dialog
   (re-do-OCR flag + instructions)
4. **Browse & trigger** (M3) — taxonomy lists with analyze / bulk-queue
5. **Jobs & settings** (M4) — batch config, apply policies, schedules,
   RAG index status, model profile overview

SPA with client-side routing; the backend serves `dist/` with an SPA
fallback so deep links survive reloads.

## Authentication

Required before the container is deployable outside a trusted network
(lands in M6). Three modes, config-selected:

1. **`none`** (default) — trusted network / VPN, current behavior.
2. **`proxy`** — trust a `Remote-User` header from an authenticating
   reverse proxy (Authelia/authentik pattern); no in-app credentials.
3. **`paperless`** — login form validated against paperless itself
   (`POST /api/token/`); no user store of our own. The per-user
   paperless token is used for that user's applied changes, so
   paperless's audit trail attributes changes to the real person and
   paperless permissions apply naturally.

Common machinery: signed httpOnly session cookie,
`/api/auth/login|logout|me`, frontend 401 interceptor + login page.
The webhook keeps its separate shared-secret header (machine-to-machine).
Multi-user is access-control only in v1: one shared workspace, no
per-user ownership of sessions/proposals (deferred until needed).

## Deployment

- Dev: `uv run` backend + `npm run dev` frontend against local services.
- Prod: OCI containers — `Containerfile`s, `compose.yaml` under `deploy/`
  (single app container; optional postgres). Podman-first; no
  docker-specific features.

## Repository layout

```
backend/
  pyproject.toml            # uv-managed
  tests/
    unit/
    integration/            # requires ad-hoc paperless (podman compose)
    live/                   # -m live_llm scenarios, opt-in
    fixtures/corpus/        # generated PDFs + taxonomy seed data
  app/
    config.py               # pydantic-settings; TOML + env
    db/                     # SQLAlchemy models, alembic migrations
    paperless/              # API client
    llm/                    # model profile factory, OCR pipeline, embeddings client
    rag/                    # VectorStore iface, sqlite-vec/pgvector backends, indexer
    agents/                 # document, tag, correspondent, doctype, explorer, shared tools
    proposals/              # schemas, apply engine, journal
    api/                    # FastAPI routers
frontend/                   # Vite + React + TS
deploy/                     # Containerfile(s), compose.yaml
  test/compose.yaml         # ad-hoc paperless-ngx for tests & manual QA
DESIGN.md
```

## Testing strategy

Tests are part of every milestone's definition of done, in three tiers:

1. **Unit** (no network; default `pytest` run, CI-blocking)
   - Paperless client against mocked HTTP (`respx`).
   - Agents against pydantic-ai `TestModel` / `FunctionModel`: scripted model
     behavior asserts tool wiring, proposal construction, revision chains,
     user-edit context injection, context clamping — no LLM involved.
   - Apply engine / journal logic against an in-memory DB.
2. **Integration** (ad-hoc paperless-ngx; local + CI)
   - Session-scoped pytest fixture boots ephemeral paperless-ngx + redis via
     `podman compose` (`deploy/test/compose.yaml`), waits for health,
     provisions an API token, seeds the fixture corpus.
   - Exercises the real API surface: search/filter semantics, `bulk_edit`,
     merge-then-delete, journal undo/restore, webhook ingress, content
     PATCH, consumption polling.
   - LLM absent or replaced by scripted `FunctionModel` → deterministic.
3. **Live-model** (opt-in, `pytest -m live_llm`; never CI)
   - Same ad-hoc paperless + seed corpus, real model endpoints from real
     config. Scenario files pair seeded state with expected outcomes
     ("DocumentAgent on invoice-003 proposes the *existing* `Deutsche
     Telekom` correspondent, not a new entity"), asserted loosely (proposal
     kind, target entity) since outputs are non-deterministic.
   - This is the automated ground truth for whether Qwen3.6-27b (or any
     future model) performs; results feed prompt iteration.

**Seed corpus as a first-class asset** (`backend/tests/fixtures/corpus/` +
a `seed` CLI command): generated PDFs (German + English — invoices,
letters, junk pages) with known content, plus a deliberately messy taxonomy
(near-duplicate correspondents, orphan tags, wrong doc types). The same
seeded instance doubles as the reset-able playground for **manual
functional testing** through the UI. The ad-hoc instance runs its own
Tesseract OCR on consumption, providing the authentic "existing OCR" that
the re-OCR comparison runs against.

Frontend: vitest + React Testing Library for proposal editor and chat
panel; Playwright e2e deferred to M6.

## Possible future extensions (explicitly OUT of the current milestones)

Ideas that keep coming up but are beyond the tool's job — getting a
paperless dataset into shape — and are parked to contain scope:

- **Freestyle explorer / "chat with the archive"**: a generic agent with
  the full toolset for open-ended querying and cross-referencing. The
  timeline/chat machinery would carry it, but it is a different product
  concern than dataset curation.
- **DSPy prompt optimization**: once reviewed proposals accumulate,
  they form labeled examples for optimizing agent prompts.
- **Token-level streaming**: pending upstream vLLM qwen3_xml streaming
  parser fixes.
- **Searchable-PDF regeneration**: would require an OCR path with
  bounding boxes (OCRmyPDF/Tesseract), distinct from the LLM pipeline.
- **Re-OCR overwrite protection**: guarding gate-accepted content
  against paperless reprocessing (journal detects it today).
- **Per-user ownership / multi-tenancy**: auth (M6) is access-control
  only; owned sessions, per-user queues etc. only if ever needed.

## Known risks

1. **27B agent reliability** — mitigated by narrow single-entity harnesses,
   capped iterations, structured `propose_*` tools, deterministic pre-passes
   for candidate generation, and human review as the backstop.
2. **Image+tools in one request unverified** on the reference stack — OCR is
   architecturally outside the tool loop, so this is never exercised.
3. **Content overwrite on paperless reprocess** — deferred; journal
   snapshots make it detectable.
4. **qwen3_xml streaming bugs** — avoided via non-streaming agent runs;
   revisit when upstream PRs land.
5. **Throughput** — page-by-page OCR is slow by design on this hardware;
   addressed by queueing, caching, and settings rather than parallelism.

## Milestones

Each milestone ships with its unit + integration tests; live-model
scenarios accumulate from M1 on.

1. **M1 — vertical slice** ✓ (shipped, evolved): config + model
   profiles, paperless client (token or credential auth), model factory,
   OCR pipeline, DocumentAgent with guarded propose tools (no-op
   rejection, referential integrity), proposals + apply engine + undo
   journal, phased document pipeline with user-gated OCR diff review,
   session-timeline UI (review queue was built and then retired in its
   favor), all-real seed corpus + ad-hoc instance + playground compose,
   test tiers (unit / integration / live).
2. **M2 — chat on the timeline**: steering as timeline chat steps
   (prose and/or superseding proposal revisions, chains rendered
   inline), SSE event stream replacing polling (event-level; token
   streaming stays off pending upstream qwen3_xml fixes), steering at
   the OCR gate ("re-run at higher DPI"), interactive-lane semantics.
   Tests: steering/revision units, SSE integration, chat component
   tests.
3. **M3 — taxonomy agents + entity matching**: Tag/Correspondent/
   DocumentType agents on the same timeline, entity embedding index
   (TEI) pulled forward for the merge-candidate pre-pass (embeddings +
   string distance) and `find_similar_entities` dedup tool, browse &
   trigger views, matching-rule proposals. Tests: merge/undo integration,
   minimal VectorStore (entity index only), taxonomy live scenarios.
4. **M4 — queueing, triggers & persistence**: celery + redis lanes
   replace the in-process spawner (stage functions unchanged), bulk
   campaigns with per-job review|auto policy, Inbox-driven trigger,
   webhook ingress, jobs & settings UI, dashboard, **alembic migrations**
   (schema settles here). Tests: webhook + job lifecycle (eager celery
   in unit tier, real worker in integration).
5. **M5 — document RAG**: document-chunk index + incremental sync,
   `semantic_search_documents`, optional reranker, PostgreSQL + pgvector
   as first-class backend. Tests: VectorStore contract suite against
   both backends, indexer sync integration, retrieval live scenarios.
6. **M6 — authentication, packaging & docs**: auth modes
   (none/proxy/paperless — see Authentication), security hardening,
   production compose + Containerfile polish, Playwright e2e smoke
   suite, user documentation.
