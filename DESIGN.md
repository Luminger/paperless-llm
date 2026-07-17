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
| Token streaming | `supports_streaming` | true — verified stable on the reference stack (earlier qwen3_xml parser concerns did not materialize); streaming feeds live progress (token counts, text tail) and per-call timing (TTFT, tok/s). The knob remains for servers with broken streaming tool-call parsing |
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
- `semantic_search_documents` (future — parked with document RAG)
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

- **Index 1 — document chunks** (future — parked with document RAG):
  existing paperless `content`, chunked, embedded, stored with doc
  metadata for filtered kNN.
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

Each stage is a **Step** (see Queueing): OCR runs as an `ocr` step whose
executor pauses at the gate (`awaiting_user`); accepting or fixing the
text resolves it and enqueues the `analysis` step. Startup recovery
re-queues steps that were interrupted mid-run.

## Sessions, proposals, steering

The core unit is the **Session** — one conversation with one agent,
optionally bound to an entity. The pydantic-ai message history is persisted
(serialized) so any session can be resumed and steered.

```
Session           agent_kind, entity_type?, entity_id?, title,
                  message_history (JSON), archived_at?;
                  phase/status are DERIVED from its steps (single writer)
Step              the timeline AND queue unit: kind (ocr|analysis|chat),
                  state, lane, params, result, attempt log, scheduled_at,
                  superseded_by?, message_range into the session history
Proposal          kind, revision, supersedes_id?, step_id,
                  agent_payload  (immutable: exactly what the model emitted)
                  user_payload?  (user's edited version, full edit visibility)
                  base_snapshot  (paperless state of touched fields at emit)
                  status: draft | pending | approved | rejected | applied |
                          superseded | no_change (apply-time: already matched)
AppliedChange     proposal_id, paperless before/after snapshots — undo journal
Job               bulk run: scope (inbox|tag|untagged|ids — deliberately
                  NO free-text-query scope: jobs run over deterministic
                  selections, not search results), apply_policy
                  (review|auto), progress counters, per-document sessions
EntityInstruction app-local per-entity agent instructions (see below)
AuditLog          data operations + paperless traffic, actor-attributed
Counter           lifetime counters (OCR runs/pages, LLM requests/tokens)
OcrResult         OCR cache keyed by (doc, checksum, model, prompt version)
```

**Steering** (`POST /sessions/{id}/messages`): appends a user message and
re-runs the agent on the stored history. The agent may answer in plain text,
emit a revised proposal (new revision superseding the old — chain preserved
and fully visible), or both. If the user hand-edited fields first, the
current `user_payload` is injected into context ("the user amended your
proposal as follows: ...") so manual fixes and "agent, fix it" compose.

Steering is **contextual, not a chat box**: there is no fixed composer.
Free-text input appears where a decision is being made — in the
proposal review UI ("ask the agent to revise"), in the redo dialog
(amended instructions), at the OCR gate — and once the initial
analysis completes, an inline "continue" affordance at the end of the
timeline offers the next free-text turn.

### Session surface (the centerpiece, specified)

One chronological step feed; every step is one card with a consistent
anatomy: header (kind · state · timestamps · timing chip) · body ·
footer (attempt history, retry/redo controls). Within an agent step's
body, the transcript renders **every part of the model exchange as a
first-class, explorable item**, in order:

- **thinking blocks** — shown collapsed ("Reasoning …"), expandable to
  the full text; never hidden entirely
- **tool calls** — collapsed to `name(short arg summary)` with status;
  expanding reveals the full arguments and the full return value
  (pretty-printed JSON), including rejected calls with their
  `ModelRetry` reason
- **text** — rendered as markdown
- **proposals** — inline editor cards (agent payload immutable, user
  payload editable), with the steering affordance attached

Collapsed by default, everything expandable — the user can audit
exactly what the model saw, did, and got back. System prompts and
steering preambles remain internal (they are configuration, not
conversation).

**Apply engine**: applies `user_payload` if present, else the latest
revision's `agent_payload`. Snapshots before-state to the journal. Merges
(correspondents/tags) are implemented as bulk reassignment
(`/api/documents/bulk_edit/`) followed by entity deletion, journaled for
undo. Per-job `auto` policy applies without review (still journaled and
revertible).

Proposal kinds (v1): `update_document_metadata`, `replace_content`,
`create_entity`, `update_entity`, `merge_entities`, `delete_entity`
(entity ∈ {tag, correspondent, document_type, storage_path}).

**Redo supersedes downstream**: redoing a step (e.g. re-running OCR with
different instructions) supersedes that step AND every step after it,
including their open proposals — later results were built on invalidated
state. Applied/rejected proposals are left alone. Superseded steps stay
inspectable on the timeline. A redo always asks how (amended
instructions/DPI/message), never silently reruns.

### Per-entity instructions

Taxonomy entities carry optional **app-local instructions** (stored in
this app, never in paperless), editable on their entity pages and shown
in taxonomy lists. The agents' `list_*` tools attach them as
`user_instructions` and the system prompt declares them binding. First
sight of paperless's inbox tag seeds a default ("remove this tag from
every document you analyze"); clearing stores an empty row, so seeded
defaults never return. The inbox tag itself is a workflow marker, not a
label — analyzing it is refused (backend 422, no UI affordance).

## Transparency & audit

- **Audit log** records data operations only (applies/reverts with
  per-field from→to diffs derived from journal snapshots, OCR gate
  acceptances, campaigns, webhook ingests) plus every paperless request —
  never app lifecycle noise. Entries are actor-attributed via a
  request-scoped contextvar (`user`, `system`; namespaced strings make
  multi-user attribution a value change, not a schema change).
  Paperless traffic is buffered in memory and flushed by a background
  writer, so the HTTP client never touches the DB.
- **Fetch transparency**: the paperless client tracks the last fetch per
  resource; list views show data age with a manual refresh.
- **Optimistic concurrency**: paperless has no revisions or etags, so
  each proposal snapshots the touched fields at emit time
  (`base_snapshot`). Apply re-checks value by value and refuses with a
  conflict detail when paperless moved underneath; fields that already
  converged to the proposed value don't conflict. If everything already
  matches, the verdict is `no_change` — nothing written, nothing
  journaled.
- **Undo**: applied proposals are revertible from the journal; a revert
  whose target already matches live paperless is refused as a no-op (and
  greyed out in the UI). Archived sessions refuse new steps and
  forward-applies (409) but reverts always remain available.
- **Lifetime counters** (OCR runs/pages, LLM requests, input/output
  tokens) accumulate atomically and surface on the dashboard.

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
2. Bulk — UI multi-select or deterministic scopes (inbox, tag,
   untagged) → job. Free-text search may aid *browsing*, but jobs are
   never defined by a search query.
3. Webhook — `POST /api/webhooks/paperless` for paperless-ngx workflow
   actions on document-added (shared-secret header).

## HTTP API surface (backend)

```
/api/sessions            paginated list (entity/archived/unfinished filters),
                         analyze/{type}/{id}, POST /{id}/messages (chat),
                         /{id}/steps/{sid}/resolve|retry|redo,
                         /{id}/archive|unarchive, GET /{id}/events (SSE)
/api/proposals           list/filter, PATCH /{id} (user edits),
                         POST /{id}/apply|reject|revert, GET /{id}/revert-check
/api/jobs                bulk jobs: create/list/detail/cancel
/api/settings            read-only config overview (M5)
/api/entities            proxied browse (+ name-resolved filters), entity
                         detail, /{type}/merge-candidates,
                         PUT /{type}/{id}/instructions
/api/webhooks/paperless  ingress (shared-secret header)
/api/audit               audit trail (actor, kind filters)
/api/sync/status         per-resource paperless fetch freshness
/api/stats  /api/meta    dashboard numbers · paperless external URL
```

SSE is an **invalidation signal, not a data transport**: two events
(`step_changed`, `step_progress`) tell the client to refetch state or
update live progress (tokens generated, tool calls, text tail). Every
model call is stamped with timing metadata (TTFT, duration, tok/s) that
travels with the persisted message history and is shown on the
transcript widgets it produced.

## Frontend

**React + Vite + TypeScript + Tailwind + shadcn/ui**, npm, TanStack
Query, SSE for live updates. shadcn/ui components are vendored source
(no runtime library lock-in) over Radix primitives — chosen in M5 for
proper menu/dialog/focus accessibility once app chrome (user menu,
settings, dialogs) entered scope; the theme tokens carry the design
language. TS API types are generated from the backend's OpenAPI schema;
a thin hand-written fetch wrapper remains. assistant-ui was evaluated
for the session surface and rejected: the timeline is a review surface
over steps/gates/proposals, not a chat thread, and our transcript
derivation already yields typed items — a chat runtime's abstractions
would fight the step model. No SSR; FastAPI serves the built `dist/` in
production, Vite dev-server proxy in development.

**The session timeline is the single review surface.** There is no
separate review queue: every analysis is a session, and everything it
produces — the OCR gate, the agent's run, and ALL proposals (metadata
updates and entity creations alike, rendered as full inline editor
cards with resolved names) — lives on one timeline page.
`/proposals/:id` deep-links redirect to the owning session.

App chrome: top nav with user icon + dropdown (about/version, link to
settings; becomes the account menu when auth lands in M6) and a
**Settings** page (read-only overview of model profiles, queue/retry
config, embeddings/webhook status — config stays file/env-driven, the
page makes it inspectable).

Views:
1. **Dashboard** (home) — sessions needing attention, lifetime counters,
   quick actions
2. **Session timeline** (centerpiece) — chronological step feed rendered
   by one generic StepCard (state, attempt history, retry/redo/resolve
   controls, live trace) with kind-specific bodies: OCR gate (editable
   diff), agent turns (transcript + inline proposal cards with resolved
   names), chat; persistent composer at the bottom; superseded steps
   stay inspectable
3. **Documents** — full-text search + name-resolved taxonomy filters,
   multiselect (cross-page) → bulk campaign; rows link to entity pages
4. **Taxonomy** — tags/correspondents/doctypes with merge candidates,
   name filter, multiselect → review sessions
5. **Entity pages** — facts, preview, instructions editor, session
   history, paperless deep link (documents and taxonomy alike; analysis
   starts here, not from lists)
6. **Jobs** — job list/detail with progress and per-doc sessions
7. **Log** — audit trail with actor, kind filters, per-field diffs

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
    fixtures/corpus/        # real, publicly shared PDFs (provenance in
                            # external/MANIFEST.md) + taxonomy seed data
  app/
    config.py               # pydantic-settings; TOML + env
    db/                     # SQLAlchemy models, alembic migrations
    paperless/              # API client
    llm/                    # model profile factory, OCR pipeline, timing wrapper
    rag/                    # (future) VectorStore iface, chunk indexer
    agents/                 # document, tag, correspondent, doctype agents + shared tools
    proposals/              # schemas, apply engine, journal
    services/               # step engine+workers, campaigns, entity index,
                            # transcripts, instructions, audit, counters, events
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
a `seed` CLI command): **real, publicly shared PDFs only** (German +
English — invoices, letters, forms, scans; provenance recorded in
`external/MANIFEST.md`), plus a deliberately messy taxonomy
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

- **Document RAG** (was M5, deprioritized): document-chunk index +
  incremental sync, `semantic_search_documents`, optional reranker,
  PostgreSQL + pgvector as first-class backend. The entity index that
  shipped with M3 covers the taxonomy use case; chunk-level retrieval
  only matters for cross-document research, which is out of scope for
  dataset curation.
- **Freestyle explorer / "chat with the archive"**: a generic agent with
  the full toolset for open-ended querying and cross-referencing. The
  timeline/chat machinery would carry it, but it is a different product
  concern than dataset curation.
- **DSPy prompt optimization**: once reviewed proposals accumulate,
  they form labeled examples for optimizing agent prompts.
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
4. **Serving-stack quirks** (streaming parsers, image limits, thinking
   modes) — contained as per-profile config; streaming can be switched
   off per profile when a server misbehaves.
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
2. **M2 — chat on the timeline** ✓ (shipped): steering as timeline chat
   steps (prose and/or superseding proposal revisions, chains rendered
   inline), SSE invalidation stream replacing polling, steering at the
   OCR gate ("re-run at higher DPI"), interactive-lane semantics. Token
   streaming was enabled after the reference stack proved stable,
   bringing live traces and per-call timing.
3. **M3 — taxonomy agents + entity matching** ✓ (shipped): Tag/
   Correspondent/DocumentType agents on the same timeline, entity
   embedding index (TEI) pulled forward for the merge-candidate
   pre-pass (embeddings + string distance) and `find_similar_entities`
   dedup tool, browse & trigger views, matching-rule proposals.
4. **M4 — queueing, triggers & persistence** ✓ (shipped, evolved): a
   **DB-backed step queue with in-process async workers** — a
   deliberate deviation from the planned celery/redis (see Queueing for
   the rationale; a distributed queue remains a contained swap). Bulk
   campaigns with per-job review|auto policy, inbox/query/untagged/ids
   scopes, webhook ingress, jobs UI + dashboard, **alembic migrations**
   (squashed to a fresh baseline pre-1.0). The Step engine later
   unified pipeline, queue, and timeline into one abstraction.
   A settings/model-profile overview UI was deferred out of M4 (moved
   to M6 packaging polish).
5. **M5 — coherence & consolidation** (replaces the RAG milestone;
   document RAG is parked under future extensions — the system is
   useful without it): one design language and one set of patterns
   across the whole API and UI, grown feature-by-feature until now.
   Decisions: shadcn/ui (vendored) for primitives; TS types generated
   from OpenAPI; "jobs" is the one name; approve flow deleted (the
   model is propose → user applies); no query-scoped jobs.
   Phased, tests green after each phase:
   - **Phase 0 — decided cleanups**: nav says Jobs; `query` job scope
     removed (tag scope added); approve route + `approved` status
     deleted (enum migration); dead surface swept.
   - **Phase 1 — API contract**: one list envelope for app-owned
     collections; explicit response schema on every route; one error
     shape `{code, message}` rendered humanely in the UI; TS types
     generated from OpenAPI (thin hand-written fetch wrapper stays).
   - **Phase 2 — design system**: shadcn/ui init themed to the
     existing zinc/emerald language; primitives (Button, Card, Table,
     Badge, Dialog, DropdownMenu, form fields, EmptyState, loading/
     error states, PageHeader) concentrate every color/spacing
     decision; central query-key registry with typed invalidation
     helpers.
   - **Phase 3 — page migration**: Dashboard, Documents, Taxonomy,
     Jobs, Log, EntityPage onto the primitives and one page scaffold
     (title · actions · filters · content); one list presentation;
     uniform dates, empty states.
   - **Phase 4 — session surface redesign** (see Session surface):
     monolith split into feature modules; thinking blocks and
     first-class explorable tool calls; fixed composer removed in
     favor of contextual steering.
   - **Phase 5 — app chrome**: top nav user menu (auth-ready for M6),
     read-only Settings page (`/api/settings`).
   - Definition of done: no color literals outside primitives, no
     unschema'd route, no raw `String(error)` in the UI, no
     hand-maintained API types.
6. **M6 — authentication, packaging & docs**: auth modes
   (none/proxy/paperless — see Authentication) wired into the M5 user
   menu, security hardening, production compose + Containerfile
   polish, Playwright e2e smoke suite, user documentation.
