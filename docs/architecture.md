# Architecture

A single app container: FastAPI backend + built React frontend, SQLite
(or PostgreSQL) for state, talking to paperless and your LLM endpoints.
No message broker, no Redis — the queue is the database.

```
┌────────────┐   REST + SSE   ┌──────────────────────────────┐
│  React SPA │ ◄────────────► │  FastAPI                     │
└────────────┘                │  ├─ step engine (DB queue,   │
                              │  │   workers, retries)       │
                              │  ├─ pipeline (OCR, agents,   │
                              │  │   decision loop)          │
                              │  ├─ proposals + apply engine │
                              │  │   + undo journal          │
                              │  └─ paperless client         │
                              └───────┬──────────────┬───────┘
                                      ▼              ▼
                               paperless-ngx   local LLM(s)
```

## The step is THE abstraction

A session is an ordered list of **steps** (OCR, analysis turn, chat
turn). The step is simultaneously:

- the **queue item** — workers claim pending steps by lane with an
  atomic state flip; two workers (or processes) can never double-claim
- the **retry unit** — attempt history, budget, backoff live on the row
- the **timeline element** — the UI renders steps; session phase and
  status are *derived* from the step list by a single writer

Generic actions (`retry`, `redo`, `resolve`) work on every step kind;
no feature reimplements queueing. `redo` supersedes the step **and
everything after it** — later results were built on state the redo
invalidates. The engine (`services/steps.py`) knows nothing about
documents; executors and gate resolvers are registered by the pipeline
(`services/pipeline.py`).

The full lifecycle — legal transitions for steps, sessions, proposals
and jobs, the no-dead-end guarantee, crash-recovery sweeps — is
normatively defined in [The state machine](state-machine.md) and
enforced in code (`STEP_TRANSITIONS`; every state write asserts its
transition).

## Agents

Built on [pydantic-ai](https://ai.pydantic.dev/). One agent per entity
kind (document, tag, correspondent, document type), each with a small
tool set: read tools (document content, taxonomy listings, similarity
search) and `propose_*` tools. Guardrails are code, not vibes:

- Proposals are validated Pydantic models; invalid or **no-op**
  proposals are rejected back to the model (`ModelRetry`) with an
  explanation, and referential integrity is checked against paperless.
- **One proposal per turn** is enforced by a tool guard, not just the
  prompt.
- Tool iterations are capped; tool results are clamped to the token
  budget; taxonomy listings attach the user's per-entity instructions.
- Document *finding* is two-stage and context-budgeted: paperless
  full-text recall, an optional local rerank, and only the top hits —
  compact summaries with snippets — reach the model. Archives scale;
  the context doesn't have to.

The OCR pipeline runs **outside** the tool loop: born-digital page
classification (`pdfio.py` — pages with a real *visible* text layer
are read straight from the PDF, no vision call), orientation
detection and rendering, batched vision calls, similarity scoring,
caching — deterministic plumbing the model can't get creative with.

## Proposals, apply, journal

The apply engine checks a proposal's **base snapshot** (the paperless
values the agent looked at) against live paperless values before
writing — value-level optimistic concurrency, since paperless has no
revisions. Applied changes store before/after snapshots in the journal;
reverts run the same machinery backwards, with their own staleness
check. Applies claim the proposal atomically, so a double-click can't
double-apply.

## Events

Server-sent events carry *invalidation signals* (`step_changed`) and
live progress (`step_progress` with structured thinking/text/tool-call
deltas). The SSE stream is an optimization: every view falls back to
REST polling when it's unavailable, and a missed event self-heals on
the next refetch.

## Storage

SQLite by default (WAL, single process); PostgreSQL via the `postgres`
extra for larger setups. Schema migrations run automatically at
startup (alembic); a test pins the migration chain to the ORM models so
they cannot drift.

## The frontend

React + Vite + Tailwind + shadcn/ui. All API types are **generated
from the backend's OpenAPI schema** — a hand-written type that drifts
from the backend is a build error, not a runtime surprise. TanStack
Query owns server state (one query-key registry, one invalidation
helper per mutation class); list/filter/pagination state lives in the
URL.
