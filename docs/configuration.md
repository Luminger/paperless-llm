# Configuration

Configuration is layered — later wins:

1. Built-in defaults
2. TOML file (`paperless-llm.toml` in the working directory, or the
   path in `PAPERLESS_LLM_CONFIG`)
3. Environment variables: prefix `PLLM_`, nesting with `__`
   (`PLLM_LLM__AGENT__BASE_URL` ≙ `llm.agent.base_url`)

A minimal TOML file:

```toml
[paperless]
base_url = "http://paperless.lan:8000"
token = "…"

[llm.agent]
base_url = "http://llm.lan:8001/v1"
model = "qwen3.6-27b"
```


## Precedence

Settings are layered — first match wins:

1. **Environment variables** (`PLLM_…`) — authoritative. A key set here
   is *locked*: the config file cannot override it (the startup log
   warns about every shadowed file value) and the Settings UI shows it
   with a lock.
2. **Settings UI** — a curated whitelist (model endpoints, sampling,
   queue brake, webhook defaults) is editable at runtime by
   administrators and persisted in the app database.
3. **Config file** (TOML, path in `PAPERLESS_LLM_CONFIG`, default
   `./paperless-llm.toml`).
4. Built-in defaults.

Deliberately *not* runtime-editable: the paperless connection,
database, auth and worker pool sizes — a bad value there would take
down the very UI needed to fix it.

## Model profiles

Every serving-setup quirk is **configuration, not code** — image
limits, concurrency, streaming support, thinking mode, sampling.

### `llm.agent` — the tool-calling chat model

| Key | Default | Meaning |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8001/v1` | OpenAI-compatible endpoint |
| `model` | `qwen3.6-27b` | Model name as the server knows it |
| `max_concurrent` | `2` | App-level cap on concurrent requests to this endpoint. Size it below the server's parallelism, leaving room for other consumers. |
| `supports_streaming` | `false` | Token-level streaming. Turn off for servers with buggy streaming tool-call parsers; the UI still updates live via events. |
| `thinking` | `server_default` | `on` / `off` sends `chat_template_kwargs`; `server_default` sends nothing. |
| `max_input_tokens` | `32768` | Used to clamp tool results (e.g. long documents), not enforced server-side. |
| `max_tool_iterations` | `12` | Cap on tool-loop rounds per agent turn. |
| `sampling.*` | server defaults | `temperature`, `top_p`, `max_tokens`, `presence_penalty` |

### `llm.ocr` — the vision model

Falls back to the agent endpoint/model when unset, so a single
multimodal endpoint needs no extra config.

| Key | Default | Meaning |
| --- | --- | --- |
| `base_url`, `model`, `api_key` | *(agent profile)* | Dedicated OCR endpoint if set |
| `max_images_per_request` | `2` | Match your server's multimodal limit (e.g. vLLM `--limit-mm-per-prompt`) |
| `max_pages` | `0` | Page cap per document (0 = all) |
| `render_dpi` | `150` | PDF page render resolution |
| `prompt_version` | `1` | Part of the OCR cache key |

OCR results are cached keyed on document + content checksum + model +
prompt, so nothing is re-transcribed needlessly — and editing the OCR
prompt in Settings invalidates exactly the right cache entries.

### `llm.embeddings` — optional

Configuring `base_url` + `model` enables the semantic entity index
(duplicate detection across tags/correspondents/types by embedding
cosine, not just string distance).

### `llm.reranker` — optional

A Cohere-compatible `/v1/rerank` endpoint (TEI and Infinity both serve
one). When configured, the agent's `find_documents` tool becomes a
two-stage retrieval: paperless full-text search recalls candidates
across the whole archive, the reranker re-orders them by actual
relevance, and only the top hits (compact summaries + short snippets)
enter the model's context — this is what keeps "find the document
about X" workable over thousands of documents on a 32–64k context.
Without a reranker the tool still works and keeps the full-text order.

| Key | Meaning |
| --- | --- |
| `llm.reranker.base_url` | e.g. `http://127.0.0.1:8091` — a bare host gets `/rerank` appended (TEI/Infinity's native path); a `…/v1` base gets `/v1/rerank` |
| `llm.reranker.model` | Served model name, e.g. `bge-reranker-v2-m3` |
| `llm.reranker.api_key` | Bearer token if the endpoint wants one |

A 0.5–1B multilingual reranker (e.g. `BAAI/bge-reranker-v2-m3` via
[Infinity](https://github.com/michaelfeil/infinity)) is enough — it
even runs fine on CPU: ~6 s for 50 candidates on a desktop Ryzen,
once per `find_documents` call.

## Paperless

| Key | Meaning |
| --- | --- |
| `paperless.base_url` | Where the *app* reaches paperless |
| `paperless.external_url` | Where *your browser* reaches paperless (UI deep links); defaults to `base_url` |
| `paperless.token` | API token for background work |
| `paperless.username` / `password` | Alternative to a token (one is fetched via `/api/token/`) |
| `paperless.verify_tls` | TLS certificate/host verification (default `true`); disable only for self-signed setups |

## Authentication

There is exactly **one** auth story: you sign in with your **paperless
credentials**. The login form is validated against paperless itself
(`POST /api/token/`) — paperless is the user store, there is no mode
matrix and nothing to configure.

Each user's applied changes run under **their own paperless token** —
paperless's audit trail names the real person and paperless
permissions apply naturally.

**Roles come from paperless too**: whoever is a *superuser* there is an
administrator here (admin rights gate settings, prompt tuning and
runtime configuration). The lookup runs under the app's background
credentials at login — those must belong to a paperless superuser,
otherwise everyone signs in as a regular user and the server log says
so. Sessions are signed httpOnly cookies
(`auth.session_hours`, default one week); the signing secret is
generated once and persisted, or set explicitly via
`auth.session_secret`.

The webhook is separate machine-to-machine auth (shared secret) and is
unaffected by user auth.

## Webhook

| Key | Default | Meaning |
| --- | --- | --- |
| `webhook.secret` | *(empty = endpoint disabled)* | Expected in the `X-PLLM-Token` header |
| `webhook.redo_ocr` | `false` | Re-OCR webhook-ingested documents |
| `webhook.apply_policy` | `review` | `review` (proposals wait for you) or `auto` (applied immediately, journaled, revertible) |

## Queue & retries

| Key | Default | Meaning |
| --- | --- | --- |
| `queue.interactive_concurrency` | `2` | Workers for chat turns / single analyses |
| `queue.batch_concurrency` | `2` | Workers for bulk jobs |
| `queue.retry_attempts` | `2` | Automatic re-runs of a failed step |
| `queue.retry_delay_seconds` | `60` | Backoff between attempts ("Retry now" in the UI overrides it) |
| `queue.auto_continuation_limit` | `10` | Runaway brake: max auto-continuation turns per autonomous (auto-apply) session |

Queue concurrency multiplies against the per-endpoint `max_concurrent`
semaphores — the model endpoint's cap is the real global limit.

## Storage

| Key | Default | Meaning |
| --- | --- | --- |
| `database_url` | `sqlite+aiosqlite:///./data/paperless_llm.sqlite3` | SQLAlchemy URL; PostgreSQL: install the `postgres` extra and use `postgresql+asyncpg://…` |
| `data_dir` | `./data` | OCR page renders and caches |

The container image defaults both to the `/data` volume.
