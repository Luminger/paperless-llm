# Configuration

## Precedence

Settings come in four layers — first match wins:

1. **Environment variables** — prefix `PLLM_`, nesting with `__`
   (`PLLM_LLM__AGENT__BASE_URL` ≙ `llm.agent.base_url`). Authoritative:
   a key set here is *locked* — the config file cannot override it (the
   startup log warns about every shadowed file value) and the Settings
   UI shows it with a lock.
2. **Settings UI** — a curated whitelist (model endpoints, OCR knobs
   and sampling, the queue brake, webhook settings) is editable at
   runtime by administrators and persisted in the app database.
3. **Config file** — TOML, path in `PAPERLESS_LLM_CONFIG`, default
   `./paperless-llm.toml` in the working directory.
4. Built-in defaults.

A minimal TOML file:

```toml
[paperless]
base_url = "http://paperless.lan:8000"
token = "…"

[llm.agent]
base_url = "http://llm.lan:8001/v1"
model = "qwen3.6-27b"
```

Deliberately *not* runtime-editable: the paperless connection,
database, auth and worker pool sizes — a bad value there would take
down the very UI needed to fix it.

## Model profiles

Every serving-setup quirk is **configuration, not code** — image
limits, concurrency, streaming support, thinking mode, sampling,
timeouts.

### `llm.agent` — the tool-calling chat model

| Key | Default | Meaning |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8001/v1` | OpenAI-compatible endpoint |
| `model` | `qwen3.6-27b` | Model name as the server knows it |
| `api_key` | `unused` | Sent as a bearer token; local endpoints usually ignore it |
| `max_concurrent` | `2` | App-level cap on concurrent requests to this endpoint. Size it below the server's parallelism, leaving room for other consumers. |
| `supports_streaming` | `false` | Token-level streaming. Turn off for servers with buggy streaming tool-call parsers; the UI still updates live via events. |
| `thinking` | `server_default` | `on` / `off` sends `chat_template_kwargs`; `server_default` sends nothing. |
| `max_input_tokens` | `32768` | Used to clamp tool results (e.g. long documents), not enforced server-side. |
| `max_tool_iterations` | `12` | Cap on tool-loop rounds per agent turn. |
| `timeout_seconds` | `600` | Wall-clock cap per LLM call, enforced app-side around the whole request (including streaming) — a wedged server fails the step into the retry machinery instead of hanging a worker forever. |
| `sampling.*` | server defaults | `temperature`, `top_p`, `top_k`, `min_p`, `max_tokens`, `presence_penalty`, `frequency_penalty`, `repetition_penalty` — standard knobs go natively, server-specific ones (`top_k`, `min_p`, `repetition_penalty`) via `extra_body` (vLLM/SGLang/llama.cpp/Ollama accept them there) |

### `llm.ocr` — the vision model

Falls back to the agent endpoint/model when unset, so a single
multimodal endpoint needs no extra config.

| Key | Default | Meaning |
| --- | --- | --- |
| `base_url`, `model`, `api_key` | *(agent profile)* | Dedicated OCR endpoint if set |
| `max_concurrent` | *(agent profile)* | Separate admission cap for a dedicated OCR endpoint. Only honored when `base_url` is set — a shared endpoint shares the agent's semaphore. |
| `timeout_seconds` | *(agent profile)* | Wall-clock cap per OCR call |
| `max_images_per_request` | `2` | Match your server's multimodal limit (e.g. vLLM `--limit-mm-per-prompt`) |
| `max_pages` | `0` | Page cap per document (0 = all); a capped run is marked truncated and never auto-resolves the gate |
| `render_dpi` | `150` | PDF page render resolution |
| `auto_rotate` | `true` | Detect flipped/sideways scans (tesseract orientation detection, when the binary is present — the container image ships it) and rotate renders upright before the vision model sees them |
| `native_text` | `true` | Born-digital gate: pages with a real visible text layer are read from the PDF directly (no VLM call); invisible OCR layers over scans still go to the VLM |
| `native_auto_accept_similarity` | `0.95` | All pages born-digital + text matches stored content at ≥ this → the OCR gate resolves itself; unset to always gate |
| `sampling.temperature` | `0.1` | The one sampling knob with a non-server default — greedy-ish decoding for faithful transcription |
| `prompt_version` | `1` | Part of the OCR cache key |

OCR results are cached keyed on document + content checksum + model +
prompt, so nothing is re-transcribed needlessly — and editing the OCR
prompt in Settings invalidates exactly the right cache entries.

#### Fighting repetition loops (`llm.ocr.sampling.*`)

Vision models sometimes hit a page they can't actually read — heavy
handwriting, stamps, degraded scans — and instead of `[illegible]` they
fall into a repetition loop, emitting the same lines until the output
context is full. That failure mode is tuned away with sampling levers,
all runtime-editable in **Settings → Models → OCR model** (hover a
label for details):

| Lever | Suggested start | Why |
| --- | --- | --- |
| `sampling.presence_penalty` | `1.0`–`1.5` | Flat penalty on already-seen tokens. Qwen-VL's own recommendation against transcription loops is up to `1.5`. |
| `sampling.repetition_penalty` | `1.05`–`1.1` | Multiplicative variant (vLLM/SGLang-style, sent via `extra_body`). Gentler on legitimately repetitive documents than a high presence penalty. |
| `sampling.max_tokens` | tok/page × images per request + headroom | Damage control: a looping page fails fast instead of generating until the server's context is exhausted. The OCR **Autodetect** button measures tok/page at your render DPI. |
| `sampling.temperature` | `0.1`–`0.3` | Pure greedy decoding (`0`) is the most loop-prone; a little randomness lets the model escape a cycle. Pair with `top_p` ≈ `0.9` or a `min_p` ≈ `0.05` to keep digits safe. |

Start with a presence penalty alone; add the others only if loops
persist. High `frequency_penalty` values distort documents that are
GENUINELY repetitive (account statements, tables) — prefer
`presence_penalty`/`repetition_penalty` for OCR. Apart from
`temperature` (see above) all knobs default to unset (server
defaults), and env vars work too, e.g.
`PLLM_LLM__OCR__SAMPLING__PRESENCE_PENALTY=1.5`.

### `llm.embeddings` — optional

Configuring `base_url` + `model` enables the semantic entity index
(duplicate detection across tags/correspondents/types by embedding
cosine, not just string distance).

| Key | Default | Meaning |
| --- | --- | --- |
| `base_url`, `model` | *(empty = disabled)* | OpenAI-compatible `/v1/embeddings` endpoint (e.g. text-embeddings-inference) |
| `api_key` | `unused` | Bearer token if the endpoint wants one |
| `dimensions` | *(unset)* | Requested vector size, for models that support truncation |
| `max_concurrent` | `4` | Declared but not currently enforced — embedding calls run as sequential batches |

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

| Key | Default | Meaning |
| --- | --- | --- |
| `paperless.base_url` | `http://127.0.0.1:8000` | Where the *app* reaches paperless |
| `paperless.external_url` | *(base_url)* | Where *your browser* reaches paperless (UI deep links) |
| `paperless.token` | | API token for background work |
| `paperless.username` / `password` | | Alternative to a token (one is fetched via `/api/token/`) |
| `paperless.timeout_seconds` | `30` | HTTP timeout for paperless requests |
| `paperless.verify_tls` | `true` | TLS certificate/host verification; disable only for self-signed setups (config/env only, never the UI) |

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
so.

| Key | Default | Meaning |
| --- | --- | --- |
| `auth.session_hours` | `168` (one week) | Signed httpOnly session cookie lifetime |
| `auth.session_secret` | *(generated)* | HMAC secret for the cookie; empty = generated once and persisted app-side (survives restarts) |
| `auth.cookie_secure` | `false` | Set the cookie's `Secure` flag — turn on for TLS deployments (the app can't reliably infer HTTPS behind a reverse proxy) |

The webhook is separate machine-to-machine auth (shared secret) and is
unaffected by user auth.

## Webhook

| Key | Default | Meaning |
| --- | --- | --- |
| `webhook.secret` | *(empty = endpoint disabled)* | Expected in the `X-PLLM-Token` header. The one-click setup on the Paperless settings tab generates one when empty |
| `webhook.public_url` | *(empty)* | Base URL **this app** is reachable at *from paperless* (e.g. `http://paperless-llm:8100` inside a compose network, or the reverse-proxy URL). Env: `PLLM_WEBHOOK__PUBLIC_URL`. Required for the one-click workflow setup |
| `webhook.redo_ocr` | `false` | Re-OCR webhook-ingested documents |
| `webhook.apply_policy` | `review` | `review` (proposals wait for you) or `auto` (applied immediately, journaled, revertible) |

All four are runtime-editable on the **Paperless** settings tab, which
also offers **Set up automatically** (creates or heals the paperless
workflow: trigger "Document Added", webhook action posting `{doc_url}`
to this app with the secret header — requires the app's paperless
account to be a superuser). The same tab shows whether the workflow's
*content* still matches the current settings — see
[Settings → Webhook ingress](usage/settings.md#webhook-ingress).

## Queue & retries

| Key | Default | Meaning |
| --- | --- | --- |
| `queue.interactive_concurrency` | `2` | Workers for chat turns / single analyses |
| `queue.batch_concurrency` | `2` | Workers for bulk jobs |
| `queue.poll_interval_seconds` | `1` | Worker poll interval over the DB queue (workers are also woken by events) |
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
