# Privacy model

paperless-llm exists because document archives are precisely the data
that should never leave your control: tax records, medical letters,
contracts, IDs.

## Privacy by construction, not by policy

The codebase contains **exactly one class of LLM integration**: an
OpenAI-compatible HTTP client pointed at a configured base URL. There
is no OpenAI/Anthropic/Google provider code, no telemetry, no
update-check phoning home. If every configured URL is on your network,
your documents cannot leave it — not because a setting says so, but
because no code path exists that could send them anywhere else.

What the app talks to, exhaustively:

| Endpoint | Data that flows there |
| --- | --- |
| Your paperless instance | Document content, metadata, files (that's its job) |
| Your LLM endpoint(s) | Document text and page images for analysis/OCR |
| Your embeddings endpoint *(optional)* | Taxonomy entity **names** only |

## What this app stores

Keeping documents out of third-party clouds does not make the app's
own database innocent — it accumulates document-derived data as a
normal part of working. An honest inventory:

| Data | Contents |
| --- | --- |
| OCR results | The **full OCR text** and per-page markdown of every document the app has transcribed (a cache, keyed by content + model + prompt) |
| Session transcripts | The agent's **complete working** — every tool call and its full result, which includes document content the agent read |
| Journal snapshots | Before/after state of every applied change; for content replacements that is the **full document text**, both versions — that's what makes revert possible |
| Proposals | Proposed metadata values and, for content proposals, proposed content |
| Audit log | Event metadata (who did what when) and per-call paperless traffic records (method, path, status — no bodies) |
| Embeddings | Vectors of taxonomy entity **names** only — never document content |

Consequence: **the app database contains your documents' text**, and
any backup of it does too. The data directory also holds runtime
secrets (the session-cookie HMAC secret, the webhook secret as a
runtime override). Treat `/data` — and every backup of it — like a
credentials file *and* like the document archive itself: restrict
access, encrypt backups.

Deleting a document in paperless does not reach into this app: its
OCR text and transcripts stay here until retention (below) removes
them.

### Retention

By default the app keeps almost everything forever; two sweeps in
[`retention.*`](configuration.md#retention) bound that:

- **`retention.orphaned_document_days`** (default `30`, on): OCR
  cache rows for documents that **no longer exist in paperless** are
  deleted once their newest row is older than the window. Detection is
  a bounded existence check per sweep (only a definitive 404 purges),
  and documents with an active session are skipped.
- **`retention.archived_session_days`** (default off, opt-in): sessions
  archived longer than the window get their transcript
  (`message_history`) blanked, and the document's OCR cache is dropped
  when no other live session needs it. The session row itself — title,
  timeline, proposals, journal — stays, and the purge is recorded in
  the audit log. Opt-in because the transcript is the "why did it do
  that?" record: destroying it is a policy decision, not a default.

What retention **never** touches: the journal. Applied changes keep
their before/after snapshots as long as they exist, archived session
or not — revertibility is a core promise. The audit log is also kept
(it holds metadata, not document content).

## Auditability

- Every applied change is journaled with before/after snapshots and an
  actor ("who did this").
- Every paperless API call the app makes is recorded in the audit log's
  *Paperless traffic* view.
- Session transcripts keep the agent's full working: reasoning, every
  tool call and its complete result. You can always answer "why did it
  do that?".

## Authentication & exposure

Signing in requires [paperless credentials](configuration.md#authentication)
— always, there is no "open" mode. Before exposing the app beyond a
trusted network, put it behind TLS (a reverse proxy). The webhook uses
its own shared secret regardless.

## What the model can and cannot do

The agent's tools are read tools plus `propose_*` tools. Writes happen
only when a proposal is applied — by you, or by an auto-apply policy
you explicitly enabled — and always through the journaled apply engine
with its staleness checks. The model has no raw HTTP access, no
paperless credentials, and no way to touch documents outside the
session's scope.
