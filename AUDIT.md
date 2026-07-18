# Full-source audit ledger (2026-07)

Findings from a five-agent parallel deep review (backend core / backend
services / backend API+proposals / frontend session+streaming / frontend
pages+shared) plus a UI-framework-adherence pass researched against
shadcn/ui + Tailwind v4 + Radix current best practices. ~18k LOC
hand-written code, all in-scope files read end-to-end; claims verified
against actual code. Baseline at audit time: 166 backend + 93 frontend
tests green.

**How to use this file:** each finding has a status line. Flip to
`FIXED (<commit>)` when resolved, `GONE (<why>)` when the code it
described no longer exists, `WONTFIX (<reason>)` when deliberately
accepted. Todo cross-refs (#66–#89) group findings into fix batches.

Severity: **critical** (none found) / **high** / **medium** / **low** /
info.

---

## Part 1 — Backend agent/LLM core (BC)

Scope: `app/agents/{runner,tools,registry,deps}.py`,
`app/llm/{ocr,rerank,factory,timing}.py`, `app/services/transcript.py`.

### BC-F1 — HIGH — streaming-IndexError fallback reuses `deps`: aborted run's proposal leaks into the re-run
- **Status:** FIXED (see commit for this file)
- **Where:** `app/agents/runner.py` fallback path + `app/agents/tools.py` `_persist` one-per-turn guard
- **Detail:** The non-streaming re-run reused the same `AgentDeps`. Tools
  executed by the aborted streaming attempt — including `propose_*` —
  have already happened: the draft `Proposal` row exists and sits in
  `deps.emitted`. Consequences: (a) the re-run's first `propose_*` is
  rejected by "One proposal per turn" and the model burns its retry
  budget on a rejection it cannot understand; (b) if the re-run finishes
  without proposing, the aborted run's proposal is still finalized
  draft→pending even though the persisted history (run 2 only) contains
  no tool call that created it — transcript and proposal list disagree;
  (c) read-tool side effects double; run-1 token usage is never counted.
- **Fix applied:** delete the aborted attempt's draft proposals and
  clear `deps.emitted` before `_run(stream=False)`; unit test covers the
  fallback with an emitted proposal. (Run-1 token usage remains
  uncounted — accepted, the run was aborted mid-response.)
- **Todo:** #70

### BC-F2 — HIGH — finalize supersede-pass can overwrite a concurrently applied proposal
- **Status:** OPEN
- **Where:** `app/agents/runner.py:156-159` + finalize loop (~236-252);
  `app/api/routes/proposals.py:99-126`; `app/db/session.py:40`
- **Detail:** `open_proposals` is loaded once at turn start; the turn can
  run for minutes. The apply endpoint has no busy-step gate (unlike
  `send_message`), so the user can apply a pending proposal while a turn
  runs. `expire_on_commit=False` means the finalize guard
  `old.status in (draft, pending)` passes on stale data and the final
  commit writes `superseded` over the `applied`/`no_change` status
  another DB session just committed. The `AppliedChange` journal row then
  points at a proposal whose status says it was never applied.
  Asymmetry: `apply_proposal` does an atomic guarded UPDATE precisely to
  avoid this; the runner's finalize does not.
- **Fix:** supersede via `UPDATE proposals SET status='superseded' WHERE
  id=:id AND status IN ('draft','pending')`; set
  `supersedes_id`/`revision` only when rowcount==1.
- **Todo:** #71

### BC-F3 — HIGH — `render_pages` blocks the event loop and holds every page PNG in memory
- **Status:** PARTIAL — preview endpoint now renders a SINGLE page via `asyncio.to_thread`; the OCR path (all pages, in-loop) is still open (#72)
- **Where:** `app/llm/ocr.py:56-67`, called at `:143`
- **Detail:** PyMuPDF rendering + PNG encoding is pure CPU, run
  synchronously in the async path. At 150 DPI a page is ~2–8 MB PNG; a
  100-page scan blocks the single event loop for many seconds (SSE
  stalls, workers can't claim, HTTP timeouts) and materializes the whole
  list — hundreds of MB — before the first LLM call, while batches need
  only `max_images_per_request` (default 2) pages at a time.
- **Fix:** `asyncio.to_thread` at minimum; better, render lazily per
  batch inside the loop at `ocr.py:152` so peak memory is one batch.
- **Todo:** #72

### BC-F4 — MEDIUM — `_int_list` raises bare `ValueError` on model garbage → whole turn fails instead of ModelRetry
- **Status:** OPEN
- **Where:** `app/agents/tools.py:37-52` (used at `:146,391,414-416,475`)
- **Detail:** `int(part)` raises on `"1, 2 and 5"`, `"none"`, `"1.5"` —
  plausible outputs from exactly the small models the helper exists to
  accommodate. A raw ValueError in a tool aborts `agent.run`, fails the
  step, and burns `retry_attempts` × 60s on a deterministic model quirk.
  `tests/unit/test_tool_coercion.py:31-33` pins the raising behavior but
  nothing converts it.
- **Fix:** wrap coercion, raise `ModelRetry("tag id lists must be
  integers, e.g. [1,2] ...")` so the model self-corrects in-turn.
- **Todo:** #73

### BC-F5 — MEDIUM — `assign_to_documents` ids never validated; reaches privileged bulk write under auto policy
- **Status:** OPEN
- **Where:** `app/agents/tools.py:442-477`; apply path
  `app/proposals/apply.py:364-372`
- **Detail:** `propose_update_document_metadata` validates every
  referenced id, but `propose_create_entity` passes
  `assign_to_documents` through unchecked. With `apply_policy="auto"`
  the ids go straight into `bulk_edit_documents`: a hallucinated-but-
  existing id silently tags an unrelated document with no human in the
  loop; a nonexistent id fails apply late instead of at propose time
  where the model could fix it. The one place unchecked model output
  reaches a write.
- **Fix:** `_require_document` each id before building the payload.
- **Todo:** #73

### BC-F6 — MEDIUM — `UpdateEntity`/`CreateEntity` built with explicit `None` kwargs defeats `exclude_unset`
- **Status:** OPEN
- **Where:** `app/agents/tools.py:519-526`, `:469-476` vs
  `app/proposals/schemas.py:4-6,126-129`
- **Detail:** Passing `name=changes.get("name"), match=changes.get(...)`
  marks every field *set*, so `exclude_unset` excludes nothing: a
  rename-only proposal persists `{"name": "X", "match": null, ...}`.
  (a) the stored payload claims the agent proposed clearing fields it
  never touched — review UI and `user_payload` editing operate on a lie;
  (b) documented "explicit None clears" semantics can never clear
  because `_entity_fields` drops None — the only way to clear a match is
  the accidental empty string. The metadata tool does it correctly via
  `model_validate(fields)` with only changed keys.
- **Fix:** build both payloads with `model_validate({...changed keys})`.
- **Todo:** #73

### BC-F7 — MEDIUM — runner failure path: no rollback before commit; no supersede pass on failure
- **Status:** FIXED (rollback-safe promotion; see commit for this file). Supersede-on-failure interplay with auto-apply tracked in #70/#74 follow-ups.
- **Where:** `app/agents/runner.py` except-path
- **Detail:** (a) If the turn failed because a tool poisoned the DB
  session (IntegrityError, counters race), the `db.commit()` in the
  except block raises `PendingRollbackError` and **masks the original
  exception** — the step's recorded error becomes the rollback error.
  (b) Drafts are promoted to pending *without* the supersede pass, so
  after failure + auto-retry the session can hold two pending proposals;
  under auto policy `_maybe_auto_apply` selects by `step_id` — the retry
  shares the step row, so attempt 1's leftover (whose transcript was
  discarded) gets auto-applied with zero review.
- **Fix applied:** promotion now rolls back on failure and re-promotes
  via a guarded UPDATE on a clean transaction; the original exception is
  always the one that propagates.
- **Todo:** #70

### BC-F8 — MEDIUM — unvalidated reranker indices → IndexError impersonating the pydantic-ai streaming bug
- **Status:** OPEN
- **Where:** `app/llm/rerank.py:278-280` + `app/agents/tools.py:185-189`
  + runner fallback
- **Detail:** `rerank()` returns `int(r["index"])` straight from the
  HTTP response; an out-of-range index makes `docs[i]` raise IndexError
  *outside* the try guarding `rerank(...)`. That propagates out of
  `agent.run` and is caught by the runner's streaming-bug handler, which
  silently re-runs the whole turn non-streaming and then fails anyway.
  The runner's `except IndexError` scope (all of agent.run incl. tool
  code) is broader than the bug it targets.
- **Fix:** filter indices to `0 <= idx < len(texts)` in `rerank()`;
  consider narrowing the runner's catch (traceback/module match).
- **Todo:** #73

### BC-F9 — MEDIUM — OCR cache upsert race across workers
- **Status:** OPEN
- **Where:** `app/llm/ocr.py:120-141,190-209`, unique `ix_ocr_key`
- **Detail:** Two steps OCRing the same document concurrently both miss
  the cache and both `db.add(OcrResult)` → second commit violates the
  unique index, step fails and auto-retries 60s later (then hits cache).
  Self-healing but costs a full duplicate OCR run + spurious failed
  attempt. Also `run_ocr` commits the caller's session mid-step
  (`ocr.py:209`) — dangerous if `ocr_document` tool is ever re-enabled
  (would persist half a turn's draft state).
- **Fix:** catch IntegrityError → rollback → re-select+update, or
  `INSERT ... ON CONFLICT DO UPDATE`.
- **Todo:** #72

### BC-F10 — MEDIUM — OCR endpoint semaphore sized by the *agent* profile; semaphore keyed on size
- **Status:** OPEN
- **Where:** `app/llm/factory.py:24-31,85-89`, `app/config.py:70-87`
- **Detail:** `OcrProfile` has no `max_concurrent`; `ocr_model()` sizes
  the OCR endpoint's semaphore with `llm.agent.max_concurrent` — wrong
  for a dedicated OCR endpoint (admission not independently tunable;
  vision concurrency ≠ chat concurrency). Semaphore key is
  `f"{base_url}#{max_concurrent}"`: `max_concurrent` is runtime-tunable,
  so a change mid-flight creates a second semaphore for the same
  endpoint (brief over-admission) and stale entries accumulate.
- **Fix:** add `max_concurrent` to OcrProfile (fallback agent's); key
  semaphores by base_url only; replace explicitly on config change.
- **Todo:** #72

### BC-F11 — MEDIUM (latent) — `ocr_document` tool would deadlock the endpoint semaphore if ever registered
- **Status:** OPEN
- **Where:** `app/agents/runner.py` run-level semaphore +
  `app/llm/ocr.py:101,158` + `app/agents/tools.py:286-302,596-608`
- **Detail:** `run_agent_turn` holds an endpoint-semaphore permit for the
  entire `agent.run`; `run_ocr` acquires from the *same* semaphore when
  the OCR profile falls back to the agent endpoint. Today unreachable
  (`ocr_document` excluded from both tool lists — dead code in
  READ_TOOLS), but armed: any future agent kind including it, with
  `max_concurrent=2` and two concurrent turns each holding a permit and
  calling the tool, deadlocks both workers forever. Holding the permit
  across tool time also under-utilizes the endpoint.
- **Fix:** release the run-level permit around tool execution, or delete
  the tool from READ_TOOLS until safe.
- **Todo:** #72 (note)

### BC-F12 — LOW — no timeout on OCR/agent LLM calls
- **Status:** OPEN
- **Where:** `app/llm/ocr.py:158-159`, runner `_run`
- **Detail:** No request timeout on either path; a wedged local vLLM
  that accepts connections but never answers stalls a worker until
  cancelled. The reranker hard-codes `timeout=60` — the only explicit
  timeout in the LLM stack, and a magic number.
- **Fix:** per-profile `timeout_seconds` (symmetry with
  `paperless.timeout_seconds`).
- **Todo:** #72

### BC-F13 — LOW — non-ModelRetry tool exceptions emit no `tool_done` SSE event
- **Status:** OPEN
- **Where:** `app/agents/registry.py:40-49`
- **Detail:** Wrapper publishes start, and done on success or ModelRetry
  — but a PaperlessError/ValueError escapes without a terminal event;
  the live UI's tool row spins until the step_changed failure
  invalidation arrives.
- **Fix:** `except Exception: publish(tool_done, error=...); raise`.
- **Todo:** #73

### BC-F14 — LOW — max-chars budget divided by 4 twice; docstring matches neither site
- **Status:** OPEN
- **Where:** `app/agents/deps.py:43-48` + `app/agents/tools.py:220,300`
- **Detail:** `max_chars` returns `max_input_tokens` verbatim while its
  docstring claims "≈4 chars/token, /4"; callers apply `// 4` again. Net:
  tool reads clamped to 8192 chars ≈ 2k tokens — 1/16 of the configured
  budget; a 200k-char document needs ~25 `get_document_content` calls
  against `max_tool_iterations=12` — long documents literally cannot be
  read within one turn.
- **Fix:** one site owns the division; fix docstring; sanity-check the
  window vs `max_tool_iterations`.
- **Todo:** #73

### BC-F15 — LOW — `search_documents` page overflow fails the turn
- **Status:** OPEN
- **Where:** `app/agents/tools.py:123-158`, `app/paperless/client.py:56-59`
- **Detail:** paperless (DRF) returns 404 "Invalid page." beyond the last
  page; `_require_document` maps 404→ModelRetry but `search_documents`
  lets PaperlessError abort the run. A model paginating past the end is
  normal behavior, not an error.
- **Fix:** map 404 to empty result or ModelRetry.
- **Todo:** #73

### BC-F16 — LOW — transcript drops non-tool retry-prompts
- **Status:** OPEN
- **Where:** `app/services/transcript.py:169-174`
- **Detail:** `retry-prompt` parts are only attached to a known
  `tool_call_id`; retries without one (output-validation failures)
  disappear — the "EVERY part is a first-class item" contract has a
  quiet exception; the user can't audit why the model repeated itself.
- **Todo:** #82 (with token-module work)

### BC-F17 — LOW — `max_pages`-truncated OCR cached as if complete
- **Status:** OPEN
- **Where:** `app/llm/ocr.py:143,175-176,190-208`
- **Detail:** With `max_pages > 0` the first-N-pages text is cached and
  similarity computed against the *full* existing content — similarity
  reads artificially low ("existing OCR is likely bad") and the cache
  row doesn't record partiality.
- **Fix:** store `truncated`/`total_pages`; surface in `OcrOutcome`.
- **Todo:** #72

### BC-F18 — LOW — `send_message` check-then-act allows two concurrent turns on one session
- **Status:** FIXED — claim skips sessions with a running step (correlated NOT EXISTS); test pins it. Cross-lane SELECT race window remains (accepted: both lanes claiming same-session steps within ms)
- **Where:** `app/api/routes/sessions.py:413-431` +
  `app/services/steps.py:367-403`
- **Detail:** Two simultaneous POSTs both pass the `blocked` SELECT and
  create two pending chat steps; `_claim` guards per-*step* not
  per-*session*, so both run concurrently: `session.message_history` is
  last-writer-wins — one turn's history silently destroyed while its
  proposals survive.
- **Fix:** claim skips steps whose session already has a running step.
- **Todo:** #76

### BC-F19 — LOW — timing depends on a pydantic-ai private field
- **Status:** OPEN
- **Where:** `app/llm/timing.py:84` (`_first_chunk_monotonic`, verified
  present in 2.12.0)
- **Detail:** Degrades gracefully (ttft=None) if upstream renames it, but
  silently. Add a unit test asserting the attribute exists so a bump
  fails loudly. Also: runner comment says the streaming bug is "still
  present in 2.13.0" while the lockfile has 2.12.0 — keep honest when
  bumping.
- **Todo:** (small; fold into #73)

### BC-C — centralization opportunities
- **Status:** OPEN — todo #82
  1. Proposal-token contract spans three modules (`tools._persist`
     builds the string, `transcript.py:26` owns the regex,
     `registry.py:58` imports the private regex) → one
     `app/proposals/tokens.py`.
  2. No-op detection & snapshot field lists exist three times
     (`tools.py:397-406`, `apply.py:137-146`, `_snapshot_conflicts`),
     incl. the `[:10]` created-date normalization twice.
  3. Matching-algorithm knowledge scattered (`tools.py:55-94` map+guard,
     `apply.py:358-361` re-encodes "6 = auto default").
  4. Prompt-addition boilerplate duplicated verbatim
     (`registry.py:188-191`, `ocr.py:113-117`, variant at `:146-147`).
  5. Tool-facing paperless error mapping: 404→ModelRetry exists only in
     `_require_document`; a shared wrapper for all read tools.
  6. Char-budget constants scattered (`// 4`s, 2000 preview, 240
     snippet, 1000 rerank window, 6000 SSE tail, 500 previews).
  7. Per-profile concurrency/timeouts: shared `EndpointProfile` base
     (base_url, api_key, max_concurrent, timeout).

---

## Part 2 — Backend services layer (SV)

Scope: `app/services/{steps,jobs,pipeline,events,runtime_config,
instructions,counters,entity_index,audit,actor,paperless_log}.py`,
`app/db/{models,session,migrations}.py`.

### SV-H1 — HIGH — auto-continuation loop can never fire from the auto-apply path
- **Status:** FIXED — `continue_after_decision(exclude_step_id=...)`; auto path passes the triggering step; tests in `tests/unit/test_pipeline.py`
- **Where:** `app/services/pipeline.py:124,141-163,193-224` (busy check
  at `:215-223`)
- **Detail:** `_maybe_auto_apply` runs inside the executor while the
  triggering step's committed state is `running`. `continue_after_decision`
  counts in-flight steps incl. `running` — the current step always
  counts, so `busy >= 1` and the continuation chat step is **never
  created** on the auto path. DESIGN.md promises autonomous continuation
  with a hard brake (max 10); `queue.auto_continuation_limit` is dead
  code. Bulk auto jobs converge after a single change per document. The
  user-driven path works (runs after finalize). No test covers
  auto-continuation.
- **Fix:** exclude the triggering step from the busy count, or move
  `_maybe_auto_apply` after finalize. Add the missing test.
- **Todo:** #74

### SV-H2 — HIGH — executor transaction holds the SQLite write lock across LLM latency; no WAL/busy_timeout
- **Status:** FIXED — WAL + busy_timeout=30s + synchronous=NORMAL + foreign_keys=ON on connect (`db/session.py`); `_persist` COMMITS the draft instead of flushing (write lock released at emit time); finalize retries transient failures (SV-L8)
- **Where:** `app/services/steps.py:405-413`, `app/agents/tools.py:355`
  (flush on proposal emit), `app/db/session.py:24-42` (no PRAGMAs
  anywhere)
- **Detail:** The first tool that emits a proposal flushes an INSERT,
  acquiring SQLite's RESERVED write lock — held until the end-of-turn
  commit, i.e. across remaining LLM iterations (minutes). Journal mode
  is default DELETE, busy timeout ~5s. Concurrent finalize hits
  "database is locked" → OperationalError → worker catch-all → **step
  stays `running` forever** (only startup `recover()` fixes it). With
  interactive 2 + batch 2 concurrency this is routine, not corner-case.
- **Fix (layered):** (1) connect-event PRAGMAs: `journal_mode=WAL`,
  `busy_timeout>=30s`, `foreign_keys=ON`; (2) commit right after the
  proposal flush to keep write transactions short; (3) in-process
  janitor for stuck-running (see SV-L8).
- **Todo:** #75

### SV-H3 — HIGH — `resolve_step` not atomic: resolver commits (and wakes workers) before the gate is marked succeeded
- **Status:** FIXED — `_resolve_ocr` creates the analysis step `commit=False`; `resolve_step` claims atomically (awaiting_user→running), commits gate+follow-up in ONE transaction, then notifies; tests pin it
- **Where:** `app/services/steps.py:278-296`;
  `app/services/pipeline.py:252-319` (`_resolve_ocr` — apply commits,
  then `create_step(...)` with default commit=True → commits + notifies)
- **Detail:** Accepting the OCR gate: (1) content proposal applied and
  committed; (2) analysis step committed, workers woken — gate still
  `awaiting_user`; (3) only then gate→succeeded. Crash/HTTP failure
  between (2) and (3): DB shows a resolvable gate PLUS a queued analysis
  step → client retry creates a second analysis step and second content
  proposal. Race without crash: worker claims analysis between (2) and
  (3); `resolve_step`'s later commit writes its stale `sync_session`
  derivation → session displayed queued/idle while a step runs (heals at
  finalize, UI lies in between).
- **Fix:** create the analysis step with `commit=False`; gate→succeeded,
  sync, ONE commit, then `notify_steps` + publish.
- **Todo:** #76

### SV-M1 — MEDIUM — `update_job` is O(sessions) + N+1 counts under a global lock on every finalize; lock doesn't prevent the lost update
- **Status:** OPEN
- **Where:** `app/services/jobs.py:401-441`; caller `steps.py:464-471`;
  read-time truth `routes/jobs.py:154-204`
- **Detail:** Every finished step loads ALL the job's sessions + one
  count per failed session — 10k-doc job ⇒ ~10^8 row loads over its
  lifetime, serialized behind one asyncio.Lock that also holds the
  worker's DB transaction (compounds SV-H2). The lock guards only to
  `flush()`; commit happens after release, so stale counters still
  commit in either order — harmless only because read-time derivation
  recomputes.
- **Fix:** delete `update_job` + stored counters in favor of the
  read-time derivation (move to services), or two aggregate GROUP BY
  queries without the lock.
- **Todo:** #77

### SV-M2 — MEDIUM — `cancel_job_steps`: non-atomic pending→cancelled flip; events published before commit
- **Status:** FIXED — guarded bulk `UPDATE ... WHERE state='pending'`; route commits, then publishes; running steps proven untouched by test
- **Where:** `app/services/steps.py:478-501`; caller
  `routes/jobs.py:277-291`
- **Detail:** SELECT-then-set without `WHERE state='pending'` guard: a
  worker can claim a step between SELECT and commit; the route's ORM
  UPDATE overwrites running→cancelled while the executor keeps running;
  finalize later overwrites cancelled→succeeded. Job does more work than
  the cancel implies; UI flaps. Also `_publish(step)` fires before the
  caller commits — violating the module's own "events never announce
  uncommitted state" rule.
- **Fix:** bulk `UPDATE ... WHERE state='pending'`, re-derive sessions,
  commit in the service, then publish.
- **Todo:** #76

### SV-M3 — MEDIUM — `create_job` per-document HTTP fetches + one mega-transaction; times out at scale
- **Status:** OPEN
- **Where:** `app/services/jobs.py:193-198,225-266`
- **Detail:** Sequential `get_document(doc_id)` for every id without a
  title (explicit-id scopes have none; tag/inbox scopes only first 100)
  → "All documents" on a 10k archive ≈ 9900 sequential GETs inside the
  POST handler before a single commit; proxy times out; user retries;
  full cost repeats.
- **Fix:** hydrate titles via `search_documents(document_ids=...)` in
  pages of 100 (`id__in` supported); consider chunked inserts.
- **Todo:** #77

### SV-M4 — MEDIUM — `counters.increment` IntegrityError recovery poisons the caller's transaction
- **Status:** OPEN
- **Where:** `app/services/counters.py:27-45`; same pattern
  `app/services/audit.py:33-43`
- **Detail:** On the insert race, `flush()` raising IntegrityError puts
  the caller-owned session into pending-rollback; the fallback UPDATE
  raises PendingRollbackError, swallowed — and every subsequent
  statement of the caller fails, so a successful agent turn is recorded
  as a failed step. The "never raises — stats must not break the
  operation" contract is inverted: it breaks it *later*.
- **Fix:** dialect upsert (`ON CONFLICT DO UPDATE value=value+delta`) or
  `begin_nested()` savepoint; same for `audit.record`.
- **Todo:** #78

### SV-M5 — MEDIUM — auto-apply ignores `archived_at`
- **Status:** FIXED — `_maybe_auto_apply` refuses archived sessions; test pins it
- **Where:** `app/services/pipeline.py:141-163`; contract in
  `db/models.py` (archived = "refuse forward-apply and new steps");
  human path enforces it (`routes/proposals.py:105-113`);
  `continue_after_decision` checks it — `_maybe_auto_apply` doesn't
- **Detail:** Archive a session belonging to a running auto-policy step
  (archive route doesn't check in-flight steps): when the turn finishes,
  `_maybe_auto_apply` applies to paperless anyway — an archived session
  forward-applied changes, violating the stated invariant.
- **Fix:** re-read `archived_at` in `_maybe_auto_apply`; arguably refuse
  archive while steps pending/running.
- **Todo:** #78

### SV-M6 — MEDIUM — `resolve_step`/`redo_step` double-resolution race
- **Status:** FIXED — resolve_step claims atomically (test); redo_step flips the redone step terminal→superseded via guarded UPDATE before creating the successor (loser 409s)
- **Where:** `app/services/steps.py:281-291` (and redo's terminal-state
  check)
- **Detail:** Two concurrent resolves both pass `state == awaiting_user`
  and both run `_resolve_ocr`; each creates its own proposal row and
  analysis step. Same check-then-act in `redo_step` (two successors).
- **Fix:** atomic flip first (`UPDATE ... WHERE state='awaiting_user'`,
  proceed on rowcount 1) — mirror `_claim`/`apply_proposal`.
- **Todo:** #76

### SV-L1 — LOW — `redo_step` publishes superseded-step events before commit
- **Status:** FIXED — supersessions published after create_step's commit — `steps.py:246-247`; same doctrine violation as
  SV-M2. Collect and publish after commit (shared commit-then-notify
  helper). Todo #76.

### SV-L2 — LOW — one global `_claim_lock` for both lanes
- **Status:** FIXED — per-lane claim locks — `steps.py:322,368`; interactive claims serialize
  behind batch claims. Per-lane locks (SQL claim is the real guard).
  Todo #75.

### SV-L3 — LOW — `recover()` is single-process only while `_claim` advertises multi-process safety
- **Status:** OPEN — `steps.py:378-380` vs `:504-540`; a second app
  process at startup clobbers the first's live steps. Guard comment or
  owner/heartbeat column before any multi-process move. Todo #75 (note).

### SV-L4 — LOW — engine paths load `Session.message_history` on every claim/finalize
- **Status:** FIXED — `defer(Session.message_history)` in claim/finalize/cancel paths (executor scope still loads it: the turn needs it) — `steps.py:388,425,464` use `db.get(Session, ...)`
  which loads the full serialized history (routes carefully `defer()`
  it). `defer(message_history)` in engine paths. Todo #75.

### SV-L5 — LOW — traffic-log writer shutdown: cancel not awaited
- **Status:** OPEN — `app/main.py:58-70`; writer may be mid-drain when
  cancelled → records lost + "Task was destroyed" warning. `cancel()`
  then `await gather(..., return_exceptions=True)` before final drain.
  Todo #78.

### SV-L6 — LOW — corpus endpoints are O(N) per call
- **Status:** OPEN — `jobs.py:80-96` (loads+JSON-parses every done
  session's params in Python), `:99-124` (worst case ~100 HTTP calls per
  click at 10k docs), hit by dashboard polling. JSON-path filter or
  denormalized `ocr_only` column; reuse the id set. Todo #77.

### SV-L7 — LOW — manual-retry attempt entry has a different shape
- **Status:** OPEN — `steps.py:198` appends `{"manual_retry_at": ...}`
  vs `{attempt, started_at, finished_at, error}` elsewhere. Unify.
  Todo #78.

### SV-L8 — LOW — no in-process recovery for a failed finalize
- **Status:** FIXED — finalize retried (0.5s/2s backoff) before giving up to recover() — `steps.py:414-474` + worker catch-all: if the
  finalize transaction itself raises, the step remains `running` until
  process restart. Bounded in-process finalize retry (idempotent) or
  periodic janitor. Todo #75.

### SV-C — centralization opportunities
- **Status:** OPEN — todos #76, #77, #82
  1. Job progress/status derivation exists 3.5× (`update_job`,
     `_live_job_counts`/`_apply_live`, stats `active_jobs`,
     `job_attention` variant) → one service function; delete
     `update_job`.
  2. `ACTIVE_PHASES` duplicated (`services/jobs.py:45`,
     `routes/jobs.py:320-322`).
  3. The "in-flight steps" predicate appears 4× with subtly different
     semantics → shared `inflight_step_ids(db, session_ids, exclude=)`
     (fixes SV-H1 too).
  4. `Proposal.kind != "replace_content"` sprinkled across ~14 sites →
     one named predicate.
  5. Commit-then-publish discipline is convention only, violated twice
     → structural helper.
  6. `PaperlessClient` construction duplicated 4× (`steps.py:59-65`,
     `api/deps.py:53`, `seeding.py:144`, `services/auth.py:144`).
  7. SQLite engine tuning belongs in `db/session.py` (nowhere for
     PRAGMAs to live today).
  8. Shared `defer(message_history)` helper for engine-side loads.

### SV — verified correct (keep)
Claim protocol (atomic UPDATE + rowcount), apply claim + unique journal
constraint, event bus (bounded queues, drop-on-full, unsubscribe in
finally), `create_step(commit=False)`+`notify_steps` batching,
`recover()` single-transaction, `_derive`/`sync_session` single-writer,
tool_lock, `make_url` sqlite path handling.

---

## Part 3 — Backend API / proposals / paperless client (API)

Scope: `app/api/**`, `app/proposals/**`, `app/paperless/**`,
`app/config.py`, `app/main.py`, `app/seeding.py`, `app/services/auth.py`.

### API-F1 — HIGH — reverting a `create_entity` proposal can delete a pre-existing entity
- **Status:** FIXED — journal carries `reused` + honest `before` snapshot; revert of a reused entity only undoes OUR assignments (tag removal / field cleared), never deletes; `revert_is_noop` knows reused semantics; test pins the 41-document scenario
- **Where:** `app/proposals/apply.py:353-362,567-570`
- **Detail:** `_apply_create_entity` deliberately **reuses** an
  identically-named entity that appeared since the proposal; the journal
  still records `before = {"entity": None}`. Revert then
  `spec.delete(...)` — deleting an entity the app never created,
  detaching every document referencing it (not just
  `assign_to_documents`). Scenario: agent proposes tag, human creates it
  meanwhile and uses it on 40 docs, apply reuses, revert deletes the tag
  for all 41 documents. The journal cannot restore this.
- **Fix:** record `reused: true` in the journal; revert of a reused
  entity only undoes the assignments, never deletes; snapshot the reused
  entity into `before`.
- **Todo:** #79

### API-F2 — HIGH — preview cache bypasses per-user paperless permissions (IDOR via shared cache)
- **Status:** FIXED — `_archived` authorizes EVERY request with the caller's client (`get_document` probe) before touching the cache; test pins the warm-cache-denied-caller case
- **Where:** `app/api/routes/entities.py:121-132`
- **Detail:** `_preview_cache` is module-global keyed by `doc_id` only.
  Once user A previews doc 42, the archived PDF bytes are served to ANY
  authenticated user — including one whose own paperless token would get
  403/404. Both `/preview` and `/preview/{page}` affected (`/thumb`,
  `/history` fine).
- **Fix:** authorization probe with the caller's client before serving
  from cache (or key by token identity); add TTL/size cap (API-F12).
- **Todo:** #80

### API-F3 — MEDIUM — bulk job scopes silently cap at 100 documents when `all` is absent
- **Status:** OPEN
- **Where:** `app/services/jobs.py:62-80`
- **Detail:** tag/inbox/all/untagged scopes make one `page_size=100`
  call; `ids = list(page.all) if page.all else [d.id for d in results]`.
  `Page.all` is documented in this codebase as present on `?query=`
  full-text searches — these scope queries send no query. If the running
  paperless doesn't include `all` on plain filtered listings, an
  "Analyze all documents" job over 5000 docs quietly processes 100 and
  reports complete. (`_docs_referencing` and `_drain` paginate
  properly.)
- **Fix:** paginate, or assert `page.all is not None` and fail loudly.
- **Todo:** #77

### API-F4 — MEDIUM — login ignores `paperless.verify_tls` and `timeout_seconds`
- **Status:** FIXED — `validate_paperless_credentials` honors both
- **Where:** `app/services/auth.py:117`
- **Detail:** `validate_paperless_credentials` builds a raw
  `httpx.AsyncClient(timeout=15)` with default TLS verification. On a
  self-signed setup with `verify_tls=false`, everything works EXCEPT
  login: the token fetch raises, is swallowed, user sees "invalid
  username or password" — misleading dead end.
- **Fix:** pass `verify=...verify_tls, timeout=...timeout_seconds`.
- **Todo:** #80

### API-F5 — MEDIUM — revert has no atomic claim; concurrent reverts double-execute
- **Status:** FIXED — guarded UPDATE on `reverted_at` claims the revert (loser gets 'already reverted'); claim released on any failure; test pins it
- **Where:** `app/api/routes/proposals.py:452-466`,
  `app/proposals/apply.py:536-547`
- **Detail:** Read-then-act: both concurrent requests load the change
  with `reverted_at IS NULL` and both issue paperless writes. For
  MergeEntities/DeleteEntity reverts that's two `spec.create` calls
  (second 400s → 502), possibly after a duplicate bulk edit.
- **Fix:** `UPDATE applied_changes SET reverted_at=? WHERE id=? AND
  reverted_at IS NULL` first; writes after the claim; clear on failure.
- **Todo:** #79

### API-F6 — MEDIUM — metadata revert always restores the `tags` snapshot
- **Status:** FIXED — snapshot includes only proposed fields; tag reverts computed as a DELTA against current tags (later paperless edits survive); tests pin title-only + delta cases
- **Where:** `app/proposals/apply.py:319,552-555`
- **Detail:** Snapshot unconditionally includes `tags`; revert replays
  every saved field — reverting a title-only proposal also PATCHes tags
  back to apply-time state, clobbering later tag changes. Also makes
  revert-noop detection stricter than the proposal warrants.
- **Fix:** include tags only when `add_tags or remove_tags`; revert tags
  as a delta (re-add removed / remove added), not a full overwrite.
- **Todo:** #79

### API-F7 — MEDIUM — SSE endpoint pins a DB session (pool slot) for the connection lifetime
- **Status:** FIXED — the session is closed explicitly right after the existence check (early close is idempotent; DI/test overrides preserved)
- **Where:** `app/api/routes/sessions.py:435-463`
- **Detail:** `session_events` takes `Depends(get_session)`; FastAPI
  closes yield-dependencies when the response finishes — i.e. when the
  SSE client disconnects, hours later. Used once for an existence check.
  Pool default 5+10: ~15 open session tabs exhaust the pool and stall
  every DB-backed request.
- **Fix:** short-lived `session_scope()` for the check; no dependency.
- **Todo:** #80

### API-F8 — MEDIUM — sessions irrevocable for up to 7 days; cookie lacks `Secure`
- **Status:** PARTIAL — `auth.cookie_secure` config added (Secure flag for TLS deployments). Server-side session invalidation (generation counter) still open — cookies remain valid until exp after logout/user-disable
- **Where:** `app/services/auth.py`, `app/api/routes/auth.py:233-240`
- **Detail:** Stateless cookie: logout only deletes the browser copy; a
  captured value (contains the user's paperless token) stays valid until
  exp (168h default). Disabling the user doesn't invalidate the baked-in
  admin role claim. `set_cookie` sets httponly+samesite=lax but never
  `secure` — no knob exists.
- **Fix:** `auth.cookie_secure` config (or derive from scheme);
  server-side session generation counter for invalidate-all; consider
  shorter default.
- **Todo:** #80

### API-F9 — LOW — malformed id lists → 500
- **Status:** OPEN — `entities.py:37-41`: `?tag_ids=1,foo` raises
  ValueError → 500 instead of 422. Shared validating dependency.
  Todo #81.

### API-F10 — LOW — proxied pagination params flow to paperless unclamped
- **Status:** OPEN — `entities.py:44-63`: `page_size=100000` makes
  paperless serialize its archive (authenticated amplification);
  page=0 → paperless 404 surfaced as `paperless_not_found`. Clamp
  page>=1, size<=100. Todo #81.

### API-F11 — LOW — webhook secret compared with `!=`; unicode digits 500
- **Status:** OPEN — `webhooks.py:55` non-constant-time compare (use
  `hmac.compare_digest`); `_extract_document_ids` `int(x)` on
  `str.isdigit()` — true for `"²"` where int() raises → 500. No rate
  limit for secret holders (accepted, comment). Todo #81.

### API-F12 — LOW — preview cache unbounded per entry, never invalidated
- **Status:** FIXED — 5-minute TTL added (4-entry cap kept); re-archived documents refresh within TTL. Per-entry size still unbounded (a PDF is what it is) — accepted.

### API-F13 — LOW — `GET /{session_id}/ocr` ignores `entity_type`
- **Status:** OPEN — `sessions.py:286-296` checks only
  `entity_id is not None`; a taxonomy session with entity_id=7 would
  look up OCR for *document* 7 — wrong-domain id reuse, nonsense diff on
  collision. Require `entity_type == document`. Todo #81.

### API-F14 — LOW — partial-apply window: paperless mutated, journal lost
- **Status:** WONTFIX (documented) — accepted window, explained in a comment at the `_apply` call site; a pre-write intent row would close it at the cost of journal noise — `apply.py:62-68,98-117`: paperless writes happen
  before the journal row commits; a crash in between releases the claim
  → retry lands in `_is_noop` → the change becomes unrevertible and
  unattributed. Rare, self-healing status-wise; document or add a
  pre-write journal-intent row if "every apply journaled" is strict.
  Todo #79 (documentation).

### API-F15 — LOW — entity-name enrichment caps documents at 100
- **Status:** OPEN — `enrich.py:238-241`: a 100-row session page bound
  to >100 distinct documents leaves some names empty, silently. Drain or
  chunk `id__in`. Todo #81.

### API-F16 — LOW — `_drain` follows absolute `next` URLs from paperless
- **Status:** OPEN — `client.py:183-193`: `next` is generated from
  paperless's Host header; httpx sends the Authorization token to
  whatever host it names — split-horizon deployments leak the token or
  fail. Re-parse and keep path+query relative to base_url. Todo #81.

### API-F17 — INFO — settings read not admin-gated
- **Status:** WONTFIX candidate — `settings.py:89,251` readable by any
  authenticated user (secrets masked). Likely deliberate single-
  household transparency; module docstring says "admin-only" — align
  doc or gate. Decide and record.

### API-F18 — INFO — misc
- **Status:** OPEN — todo #81
  - `sessions.py:239` `assert s is not None` vanishes under `python -O`.
  - Unknown `/api/...` GETs fall through to SPA `index.html` 200 instead
    of JSON 404.
  - No login rate limiting (paperless does the real check; LAN
    deployment mitigates; audited at least).
  - First-boot `session_secret` read-then-insert race across processes
    (theoretical, single-process).
  - `parse_cookie` non-numeric exp → 500 (unreachable without HMAC key).
  - `prefs.py:194` `time_zone` accepts any string; validate against
    `zoneinfo.available_timezones()`.

### API-C — centralization opportunities
- **Status:** OPEN — todo #82
  1. Per-session proposal counts (CASE expressions + stitching)
     duplicated `sessions.py:99-135` / `jobs.py:244-283`;
     `kind != "replace_content"` ×6 here alone → `proposal_counts()`
     helper + `VISIBLE_PROPOSALS` constant.
  2. Entity-name back-fill loop repeated 3× → `apply_entity_names()`.
  3. PaperlessClient lifecycle: per-request client construction + 4
     background variants → one factory (F4 shows why).
  4. Proxied-pagination clamps shared with `pagination.py`.
  5. Snapshot/revert field tuple appears 4× in `apply.py` →
     `ENTITY_REVERT_FIELDS`.
  6. `_id_list` → shared validating dependency (fixes API-F9).

### API — verified correct (keep)
Router-level auth complete (only auth/webhooks unguarded, by design);
admin gating on config writes + prompt keys; apply claim atomicity; SPA
fallback traversal-safe; pagination clamping for DB lists; config
layering (env-locked 409, whole-object validation, secrets masked);
token hygiene (never logged, `/api/token` excluded from audit);
webhook disabled without secret.

---

## Part 4 — Frontend session / streaming (FS)

Scope: `features/session/*`, `useSessionEvents.ts`, `ProposalCard.tsx`,
`SessionList.tsx`, `DiffView.tsx`, `StatusBadge.tsx`,
`SessionDetail.tsx`. Backend event contract read for verification.

### FS-1 — HIGH — stale OCR text can be seeded into the gate after a re-run
- **Status:** FIXED — `keys.sessionOcr(sessionId, stepId)` step-scoped + `OcrGateBody key={step.id}` (edit state can't survive across steps)
- **Where:** `features/session/OcrGate.tsx:15-38`; `lib/keys.ts:8`
  (`sessionOcr` session-scoped, not step-scoped)
- **Detail:** `OcrGateBody` seeds editable text once
  (`if (ocr && newText === null)`). After "Re-run OCR", step A is
  superseded, step B reaches `awaiting_user`; with no mounted observer
  the invalidation only marks stale — the gate for B mounts, useQuery
  returns the cached step-A payload synchronously, `newText` seeds from
  step A's text, and the `=== null` guard prevents resync when B's data
  arrives. The user can Accept the pre-re-run text; the re-run's output
  is silently discarded. Data-affecting.
- **Fix:** step-scoped key `sessionOcr(sessionId, stepId)` (SSE prefix
  invalidation still matches) and/or `key={step.id}` on OcrGateBody.
- **Todo:** #83

### FS-2 — MEDIUM — `tool_done` matches backwards: parallel same-name tool calls swap results
- **Status:** OPEN
- **Where:** `hooks/useSessionEvents.ts:95-110`; backend
  `registry.py:24-68`
- **Detail:** Start events publish before the tool_lock, done events in
  lock (FIFO) order; the reducer matches `tool_done` scanning from the
  END → A's result attaches to B's row and vice versa, including
  `proposal_id` attribution. Self-heals when the finished transcript
  replaces live items.
- **Fix:** scan forward (FIFO); long-term carry pydantic-ai's
  `tool_call_id` in both events (also deletes the gen counter — FS-3).
- **Todo:** #84

### FS-3 — MEDIUM — reconnect / dropped events desync the `gen` counter → scrambled live timeline
- **Status:** OPEN
- **Where:** `useSessionEvents.ts:56-64,137-160`; bus has no replay
  (`events.py:22-46` QueueFull drops)
- **Detail:** `gen` only increments on a *received* tool event. Missed
  tool events during an outage → reconnect streams request 6's part 0,
  client computes `g2:part:0` which matches request 3's rendered item →
  in-place overwrite above older rows — exactly the scrambled timeline
  the counter exists to prevent. `onopen` refetches REST but never
  touches `live`.
- **Fix:** drop live state on `es.onopen` (server accumulates part
  content, so a running step's prose reappears next flush).
- **Todo:** #84

### FS-4 — MEDIUM — live transcript flickers away at step completion
- **Status:** OPEN
- **Where:** `useSessionEvents.ts:178-186` + `StepCard.tsx:283-297`
- **Detail:** On `step_changed` (non-running) the hook deletes the live
  entry immediately, then invalidates; until the refetch lands the
  cached step is still `running` → `streaming=true`, `items=[]` — the
  whole streamed transcript vanishes to a "working…" pulse, then pops
  back. Also the memory-growth path: entries whose step_changed was
  dropped linger until unmount.
- **Fix:** state-driven pruning (compare refetched steps to live keys)
  or fall back to live.items while transcript empty.
- **Todo:** #84

### FS-5 — MEDIUM — proposal decided under the user: Save stays live; panel folds mid-edit
- **Status:** OPEN
- **Where:** `ProposalCard.tsx` action row; `StepCard.tsx`
  `renderProposal` `defaultOpen`
- **Detail:** `editable` correctly disables inputs on status change, but
  Save/Discard are gated only on `dirty` — `save.mutate` can PATCH an
  already-decided proposal. And the Panel's
  `defaultOpen={status !== applied && !== no_change}` flips on the same
  refetch, so the `<details>` closes itself while the user is typing.
- **Fix:** gate on `editable && dirty`; "decided while you were editing"
  notice; suppress self-fold while dirty.
- **Todo:** #85

### FS-6 — MEDIUM — unbounded OCR diffs can freeze the tab
- **Status:** OPEN
- **Where:** `DiffView.tsx`; call sites `StepCard.tsx` OcrBody (open by
  default), `OcrGate.tsx:47`
- **Detail:** `DiffMethod.WORDS` renders every line of both documents
  into the DOM (max-h only clips scrolling) and word-diffs per changed
  block. Content of a large scan is easily hundreds of KB — session load
  for a 300-page document blocks the main thread seconds-to-minutes.
- **Fix:** size guard: above N lines/chars drop to `DiffMethod.LINES`
  behind a "show full diff (large)" fold; hard cap with lazy expansion.
- **Todo:** #85

### FS-7 — LOW — `NextTurnBox` can wedge on "sent" text
- **Status:** OPEN — `ContinueBox.tsx:29-45`: `sent` never cleared on
  success and the component has no key; if the chat step completes
  before any refetch observes it busy, the box stays frozen with no
  input until reload. `key={turnNo}`. Todo #84.

### FS-8 — LOW — scheduled-retry steps show a live "working…" pulse
- **Status:** OPEN — `StepCard.tsx`: `pending` + future `scheduled_at`
  treated as streaming — empty pulse, and the failed attempt's
  transcript hidden until the retry runs. Treat as non-streaming or
  caption "retry scheduled". Todo #84.

### FS-9 — LOW — `tokenizeRefs` rewrites tokens inside code spans/fences
- **Status:** OPEN — `RefChip.tsx:26-33`: pre-parse string rewrite hits
  tokens the model quotes in code blocks → literal
  `[tag](pllm://tag/5)` rendered. Move to a remark AST plugin (text
  nodes only) or document. Todo #85.

### FS-10 — LOW — positional keys remount work-folds/rows, losing toggle state
- **Status:** OPEN — `StepCard.tsx` `key={fold-${out.length}}`,
  `Transcript.tsx` `key={i}` within slices; recomposition shifts
  positions → user-expanded folds snap shut during live runs. Stable
  keys from content identity (`live_key ?? ts`). Todo #84.

### FS-11 — LOW — work-fold count label counts items the transcript hides
- **Status:** OPEN — WorkFold counts vs `Transcript`'s
  `!item.content.trim()` filter — label can claim more rows than shown.
  Shared `isRenderable(item)`. Todo #84/#89.

### FS-12 — LOW — `RedoDialog` sends `NaN` for non-numeric DPI
- **Status:** OPEN — `RedoDialog.tsx:96-101`: "300dpi" → Number() NaN →
  serialized null. `Number.isFinite` validation + disabled confirm.
  Todo #85.

### FS-13 — LOW — double refetch per reconnect; endless 3s reconnect loop on closed streams
- **Status:** OPEN — `useSessionEvents.ts:139-159`: onopen invalidates +
  hello invalidates again (two fetches per reconnect); server-closed
  streams reconnect every 3s forever even for finished sessions in
  background tabs. Let hello be the single trigger; back-off/stop for
  terminal sessions. Todo #84.

### FS-14 — LOW — clickable step header not keyboard-accessible
- **Status:** OPEN — `StepCard.tsx` header `role="button"` without
  tabIndex/keydown. Fixed together with UI-U2 (Collapsible rebuild).
  Todo #67.

### FS-15 — notes / verified OK
- Markdown injection safe (no rehype-raw; urlTransform whitelists
  pllm://, defers rest to defaultUrlTransform; noreferrer/_blank).
- Reducer robustness: unmatched tool_done no-op; malformed SSE dropped.
- Panel `<details open>` trick correct (attribute rewritten only on prop
  change).
- Live `args` >500 chars truncate mid-JSON → parse fails → live row
  shows `()` until transcript lands (registry.py:33-35) — cosmetic.
- `stepProposals` trusts `result.proposal_ids`; proposal missing from it
  is invisible on finished steps — invariant worth a comment.
- `aggregateTimings` dedupes by `started_at|finished_at` — identical
  stamps would collapse (acceptable).
- Framed SessionList mounts the archived list eagerly inside the
  collapsed FramedCard (extra query per detail page); non-framed branch
  is lazy — minor inconsistency. Todo #88.
- `useSessionEvents.test.ts:47` comment says "FOUR items" but asserts
  three — comment drift. Todo #88.

### FS-C — centralization opportunities
- **Status:** OPEN — todo #89
  1. Proposal-kind dispatch scattered (`kind !== "replace_content"` ×3
     in StepCard + EntityPage variant; editor selection; labels) → one
     `proposal-kinds.ts` registry `{label, editor, internal,
     hasBaseColumn}`.
  2. `deriveTurnView(step, proposals, live)` pure fn — three parallel
     live-vs-finished switches in TurnBody today.
  3. `isRenderable(item)` shared predicate.
  4. Tool-call identity: one backend `tool_call_id` field deletes the
     FIFO heuristic AND the gen counter (FS-2, FS-3).
  5. `keys.sessionOcr` step-scoping in the central registry (FS-1).
  6. Step-kind exhaustiveness (`Record<Step["kind"], …>`) is good —
     replicate for proposal kinds.

---

## Part 5 — Frontend pages + shared components (FP)

Scope: App/main/api, lib/*, hooks, all pages, `components/app/*`,
`components/settings/*`, Pager, FetchStatus, MultiFilter, selection.

### FP-H1 — HIGH — taxonomy selection survives a type switch → bulk analyze can submit the WRONG entities
- **Status:** FIXED — `useSelection(scopeKey)` self-clears on scope change (render-time adjustment); Taxonomy passes the type; hook test pins it
- **Where:** `pages/Taxonomy.tsx:85` (`useSelection()` — plain useState,
  `components/app/selection.tsx:9-31`); route `/taxonomy/:type` single
  element
- **Detail:** Same route pattern → no remount on type switch; selection
  {3,7,12} made on tags renders checked on correspondents (per-table id
  overlap near-certain), SelectionBar says "3 selected", and "Analyze 3
  correspondent(s)" posts `entity_type: "correspondent",
  entity_ids: [3,7,12]` — entities the user never picked.
- **Fix:** `useSelection(scopeKey)` self-clearing on scope change (or
  keyed remount).
- **Todo:** #86

### FP-H2 — HIGH — Documents: local search state resurrects a cleared URL query
- **Status:** FIXED — shared `components/app/UrlSearchInput.tsx` (URL is source of truth, local state is an edit buffer); Documents rewired; tests incl. the no-resurrection case
- **Where:** `pages/Documents.tsx:47,53-57`
- **Detail:** Nav-click to bare `/documents` doesn't remount; local
  `query` still holds "invoice"; the debounce effect sees
  `query !== submitted` and patches `q=invoice` back after 350ms — the
  explicit navigation to the unfiltered list is undone; the input never
  visually clears. (ResetFilters only works because it calls
  `setQuery("")` by hand.)
- **Fix:** reconcile external URL changes into the input (edit-buffer
  pattern) — best as a shared `<UrlSearchInput>`.
- **Todo:** #86

### FP-M1 — MEDIUM — server-hydrated prefs never re-render mounted consumers; `usePrefsTick` has zero consumers
- **Status:** OPEN
- **Where:** `lib/prefs.tsx:14-31`
- **Detail:** Exported `usePrefsTick` imported nowhere (verified);
  provider bump only re-renders consumers, and children are referentially
  identical. Stale-localStorage browser renders wrong-timezone
  timestamps until an unrelated re-render; settings changes don't update
  the background page live.
- **Fix:** subscribe date formatting via `useSyncExternalStore`
  (`useDateFormat()` / `<DateTime>`); DateTimePrefs.update bumps the
  store.
- **Todo:** #87

### FP-M2 — MEDIUM — invalid timezone from server prefs crashes every date render; no error boundary exists
- **Status:** OPEN
- **Where:** `lib/format.ts:118-133,89-97`; no ErrorBoundary anywhere
  (grep zero hits)
- **Detail:** `new Intl.DateTimeFormat(..., {timeZone})` throws
  RangeError in render for a zone unsupported by the current browser;
  white-screen on every page showing a date; bad value persisted in
  localStorage AND server. `time` pref not validated at all (`date` is).
- **Fix:** try/catch with UTC/browser fallback; validate `time`;
  app-level error boundary.
- **Todo:** #87

### FP-M3 — MEDIUM — cancel-job failures completely silent (both surfaces)
- **Status:** OPEN
- **Where:** `pages/Jobs.tsx:205-211`, `pages/JobDetail.tsx:88-94`;
  ConfirmDialog has no error slot
- **Detail:** POST fails (409/500) → dialog sits there, destructive
  button enabled, nothing rendered. The modal-guard pattern exists
  precisely for cancel; failing silently inside it is a hole.
- **Fix:** error slot in ConfirmDialog (mirror StartJobDialog), pass
  `cancel.error`. Folded into the AlertDialog rebuild.
- **Todo:** #68

### FP-M4 — MEDIUM — `useUrlPatch` deletes any value that stringifies to "0"
- **Status:** FIXED — blanket rule dropped (numeric params own their zero-defaults); "0" searchable, test pins it
- **Where:** `hooks/useUrlState.ts:57`
- **Detail:** Typing `0` into the taxonomy name filter does nothing;
  searching "0" in Documents triggers an endless 350ms patch loop
  (write→delete→rearm). `useUrlNumber` already maps 0→"" itself; the
  blanket rule protects nothing.
- **Fix:** drop the `str === "0"` rule; keep `!str` and the `page==="1"`
  rule.
- **Todo:** #86

### FP-M5 — MEDIUM — AuthProvider: HTTP-level auth failure is an infinite skeleton
- **Status:** OPEN
- **Where:** `lib/auth.tsx:36-42`
- **Detail:** `/api/auth/me` 500 → `me` undefined, retries exhaust, user
  sits on skeleton forever. ConnectivityProvider only rescues TypeError
  network failures, so nothing refetches.
- **Fix:** render ErrorNotice + retry on query error state.
- **Todo:** #87

### FP-M6 — MEDIUM — EntityPage instructions keyed on server value can discard in-flight edits
- **Status:** OPEN
- **Where:** `pages/EntityPage.tsx:470`
- **Detail:** `key` includes `entityQuery.data.instructions`; Save →
  invalidate → refetch returns saved text → key changes → editor
  remounts, discarding everything typed after Save. Any background
  refetch that changes instructions nukes the draft.
- **Fix:** key by `${entityType}-${id}` only; reconcile initial via
  effect when not dirty.
- **Todo:** #87

### FP-L1 — LOW — `useUrlState` header contradicts `replace: true` behavior
- **Status:** OPEN — comment promises back/forward filter history; every
  write replaces. Correct the comment or push for discrete changes.
  Todo #88.

### FP-L2 — LOW — out-of-range page shows misleading "no results"
- **Status:** OPEN — `Documents.tsx:152-157`, `Taxonomy.tsx:182`,
  `Pager.tsx:54`: deep link `?page=40` after shrink → "No documents
  match" while the count disagrees. Clamp to last page or say "page out
  of range". Todo #88.

### FP-L3 — LOW — `SessionList`/`PagedList` page state never resets on entity change
- **Status:** FIXED — SessionList keyed by `entityType:id` on EntityPage — `SessionList.tsx:152` local useState survives
  same-route id changes → page 3 of one entity's sessions shown for the
  next entity. Key by entity identity. Todo #86.

### FP-L4 — LOW — `DocumentViewerDialog` bypasses api.ts and keys.ts
- **Status:** OPEN — `DocumentPreview.tsx:73-78`: raw fetch, no r.ok
  check (500 JSON → pages undefined), 401 doesn't dispatch
  `pllm:unauthorized`, ad-hoc query key. Add
  `getDocumentPreviewInfo`/`keys.documentPreview`. Todo #88.

### FP-L5 — LOW — FramedCard fold toggle mouse-only
- **Status:** OPEN — duplicate of UI-U2; fixed by the Collapsible
  rebuild. Todo #67.

### FP-L6 — LOW — JobDetail polls attention every 5s forever
- **Status:** OPEN — `JobDetail.tsx:32` unconditional refetchInterval
  (job query itself stops correctly). Gate on job active/remaining.
  Todo #88.

### FP-L7 — LOW — silent failures on smaller mutations
- **Status:** OPEN — EditableTitle rename (`SessionDetail.tsx:130-137`),
  SessionRow archive toggle (`SessionList.tsx:24-28`), dashboard
  inbox/corpus blocks return null on query error (HTTP 500 hides work
  items). Thread ErrorNotice. Todo #88.

### FP-L8 — LOW — `DateTimePrefs.exampleFor` mutates global pref state during render
- **Status:** OPEN — `DateTimePrefs.tsx:29-40,96,106`: temporary
  localStorage write + formatter-cache thrash during render, ~14
  rebuilds/s while the ticking modal is open. Pure
  `formatWith(prefs, date)`. Todo #87.

### FP-L9 — LOW — misc consistency nits
- **Status:** OPEN — todo #88
  - `SystemInfo.tsx:15` ad-hoc `["meta"]` key vs `keys.meta()`.
  - `Jobs.tsx:180` hard-coded page size 25, no size control (only
    top-level list without it).
  - `AuditLog.tsx` missing LoadingState skeleton on first load.
  - `format.ts:119-120` dead `"system"` branch.
  - `App.tsx:135-139` opening Settings while `/settings` open pushes a
    second entry → must close twice; guard `if (settingsOpen) return`.
  - StartJobDialog state (auto/instructions/stale error) persists across
    close/reopen on the dashboard (Jobs.tsx gates mount; dashboard
    doesn't).

### FP — verified correct (keep)
Batched URL updates via one `useUrlPatch` call everywhere; query keys
carry their params with centralized invalidation helpers; timezone math
DST-correct via formatToParts + h23 (plain dates never shifted, tested);
auth flow (single 401 event, staleTime Infinity + invalidate, no
redirect loops, SSE death degrades to poll); modal guards on schedule +
cancel; no raw IDs user-facing; 93/93 tests.

### FP-C — centralization opportunities
- **Status:** OPEN — todos #86, #87, #88, #89
  1. `useListPage` / `<ListShell>` — four pages hand-assemble URL params
     + query + Pager + FetchStatus with drift (Jobs no size control,
     AuditLog no skeleton, Taxonomy client-side slice).
  2. `<UrlSearchInput param="q">` owning debounce + external
     reconciliation (FP-H2).
  3. `useSelection(scopeKey)` self-clearing (FP-H1).
  4. `<DateTime>`/`useDateFormat()` subscribed via useSyncExternalStore
     (FP-M1/M2/L8 in one).
  5. Shared dialog error slot (FP-M3).
  6. api.ts/keys.ts discipline for binary/preview endpoints + CI grep
     for ad-hoc queryKey literals (2 violations exist).
  7. Badge/label/actor maps converging (KIND_COLORS, StatusBadge.colors,
     HISTORY_KINDS, proposalKindLabel, Actor/ActorBadge) →
     `lib/labels.ts`.

---

## Part 6 — UI framework adherence (UI)

Researched: shadcn/ui theming + components docs (semantic tokens,
`:root`/`.dark` pairs, AlertDialog-for-destructive, Sonner guidance,
Collapsible vs native details, Oct-2025 component batch), Tailwind v4
`@theme inline` pattern, MDN disclosure semantics.

### UI-U1 — HIGH — status-color system bypasses the token system
- **Status:** OPEN
- **Where:** ~60 raw palette utilities with hand-paired `dark:` variants
  across 11 files: `StatusBadge.tsx` (header claims "dark-mode variants
  live here and nowhere else" — no longer true), `AuditLog.tsx`
  KIND_COLORS, `FetchStatus.tsx`, `ConnectionToast.tsx`, `Jobs.tsx`,
  `Taxonomy.tsx`, `Login.tsx`, `Transcript.tsx`, `StepCard.tsx`,
  `ProposalCard.tsx`, `PromptTuning.tsx`
- **Detail:** shadcn rule: raw colors only in token definitions;
  semantic tokens in components. Every
  `bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300`
  quadruple is a drift site — and they HAVE drifted (`text-red-700` vs
  `text-red-800` between files).
- **Fix:** `--success`/`--warning`/`--info` (+foregrounds) in
  `:root`/`.dark` + `@theme inline`; replace quadruples with token
  classes; merge label maps into `lib/labels.ts`.
- **Todo:** #66

### UI-U2 — HIGH — FramedCard (THE box) hand-rolls disclosure with div+onClick
- **Status:** OPEN
- **Where:** `components/app/Framed.tsx` header; `StepCard.tsx` header
  (`role="button"` no tabIndex/keydown — FS-14)
- **Detail:** No keyboard access, no aria-expanded, invisible to AT — on
  every detail page and every trace turn — while `ui/collapsible` sits
  vendored (used once) and `Panel.tsx` shows the native-details
  alternative done right.
- **Fix:** rebuild FramedCard header on Collapsible/CollapsibleTrigger,
  pixel-identical strip; same treatment for StepCard header.
- **Todo:** #67

### UI-U3 — MEDIUM — ConfirmDialog uses Dialog; destructive confirmation wants AlertDialog
- **Status:** OPEN
- **Where:** `components/app/ConfirmDialog.tsx`
- **Detail:** Radix AlertDialog gives role=alertdialog, initial focus on
  Cancel, no outside-click/Esc accidental dismissal — meaningful for
  "Cancel the job". The `radix-ui` package already ships it; vendor
  `alert-dialog.tsx`, swap internals, keep public API. Natural place for
  the FP-M3 error slot.
- **Todo:** #68

### UI-U4 — MEDIUM — tooltip affordance split
- **Status:** OPEN — `ui/tooltip` used once (RefChip) vs 44 native
  `title=` attrs — no keyboard/touch access, inconsistent look. Adopt
  the wrapper for interactive elements; `title=` only for redundant
  hints. Todo #69.

### UI-U5 — LOW — components.json declares the wrong icon library
- **Status:** OPEN — `"iconLibrary": "hugeicons"` vs project law
  lucide-react; future `shadcn add` would scaffold unresolvable imports.
  One-line fix. Todo #69.

### UI-U6 — LOW — ConnectionToast lacks aria-live
- **Status:** OPEN — hand-rolled toast (deliberate) is silent to screen
  readers; add `role="status" aria-live="polite"`. Todo #69.

### UI — verified canonical (keep)
Tailwind v4 token plumbing (`@theme inline`, oklch `:root`/`.dark`,
`@custom-variant dark`); custom ThemeProvider correct for Vite
(next-themes is Next-only) incl. live prefers-color-scheme tracking;
`SimpleSelect`→ui/select single wrapper; `Pager`→ui/pagination;
MultiFilter on DropdownMenuCheckboxItem (idiomatic); `DateField`→
calendar+popover; `states.tsx` with role=status/alert; `Panel.tsx`
native details; vendoring insulates from upstream's Base UI migration.
