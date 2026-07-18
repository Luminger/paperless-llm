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

## Auditability

- Every applied change is journaled with before/after snapshots and an
  actor ("who did this").
- Every paperless API call the app makes is recorded in the audit log's
  *Paperless traffic* view.
- Session transcripts keep the agent's full working: reasoning, every
  tool call and its complete result. You can always answer "why did it
  do that?".

## Authentication & exposure

The default (`auth.mode = none`) assumes a trusted network. Before
exposing the app beyond it, enable [an auth mode](configuration.md#authentication)
— proxy-based or paperless-account login — and put it behind TLS (a
reverse proxy). The webhook uses its own shared secret regardless.

## What the model can and cannot do

The agent's tools are read tools plus `propose_*` tools. Writes happen
only when a proposal is applied — by you, or by an auto-apply policy
you explicitly enabled — and always through the journaled apply engine
with its staleness checks. The model has no raw HTTP access, no
paperless credentials, and no way to touch documents outside the
session's scope.
