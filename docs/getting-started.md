# Getting started

## What you need

| Piece | Notes |
| --- | --- |
| **paperless-ngx** | Any recent version; you need its URL and an API token (paperless → *Settings → My profile*). The token's account should be a **superuser** — that is what makes you an administrator here, and what the one-click webhook setup needs. |
| **A local LLM endpoint** | Anything OpenAI-compatible: vLLM, llama.cpp server, LM Studio, Ollama (`/v1`), … A tool-calling chat model in the ~20–30B class works well (developed against Qwen3.6-27B). |
| **A vision model** *(optional)* | For the OCR pipeline. Can be the same endpoint if the model is multimodal, or a dedicated OCR model. Without it, re-OCR simply isn't offered. |
| **An embeddings endpoint** *(optional)* | Enables semantic duplicate detection in the taxonomy tools (e.g. text-embeddings-inference). |
| **podman or docker** | With the compose plugin. Everything below says `podman compose`; `docker compose` works identically. |

!!! warning "Container networking"
    Every URL you configure must be reachable **from inside the
    container**. `127.0.0.1` refers to the container itself — use your
    host's LAN name/address, or host networking.

## Run it

```bash
git clone https://github.com/Luminger/paperless-llm.git
cd paperless-llm/deploy/production
cp .env.example .env
$EDITOR .env
podman compose up -d
```

The `.env` boils down to four required lines:

```bash
PLLM_PAPERLESS_BASE_URL=http://paperless.example.lan:8000
PLLM_PAPERLESS_TOKEN=…            # superuser account, see above
PLLM_AGENT_BASE_URL=http://llm.example.lan:8001/v1
PLLM_AGENT_MODEL=qwen3.6-27b
```

Open `http://your-host:8100` and **sign in with your paperless
credentials** — paperless is the user store, there is nothing to set
up. Paperless superusers are administrators here; everyone else is a
regular user. Details in
[Configuration → Authentication](configuration.md#authentication).

The app keeps all of its state (SQLite database, OCR cache) in the
`appdata` volume. Your paperless data is never touched except through
paperless's own API, and only when you apply a proposal.

Before the first real run, **Settings → Models** is worth a minute:
every model card has a **Test connection** button (the OCR profile is
tested with an image attached), and **Autodetect** fills in sensible
context/image limits measured against your actual server. See
[Settings → Models](usage/settings.md#models).

## First analysis

1. **Documents** → pick a document → its page offers two actions:
   **Start analysis** (metadata proposals) and **Re-do OCR**
   (re-transcribe only, no analysis).
2. If you chose **Re-do OCR** — or checked *re-do OCR first* in a bulk
   dialog — the pipeline stops at the [OCR gate](usage/sessions.md#the-ocr-gate):
   a side-by-side diff of the stored text and the fresh transcription.
   Accept, hand-fix, or keep the existing content. Born-digital PDFs
   skip the vision model entirely and, when nothing changed, resolve
   the gate on their own.
3. Watch the agent work — reasoning and tool calls stream live.
4. A proposal card appears. Edit any field if you like, then **Apply**.
5. The session continues by itself until the document is done.

## Hooking up the inbox

Two options, freely combined:

- **Bulk job**: multi-select on the Documents page, the dashboard's
  inbox card, or the inbox/untagged scopes on the Jobs page.
- **Webhook**: paperless posts every newly consumed document to this
  app. Setup is one click: in **Settings → Paperless → Webhook
  ingress**, set *This app's URL (as paperless sees it)* (e.g.
  `http://paperless-llm:8100` inside a compose network) and press
  **Set up automatically** — the paperless workflow (trigger "Document
  Added" → webhook) is created for you, secret included. With the
  production compose, set `PLLM_WEBHOOK_SECRET` in `.env` first (any
  random string). New documents then get analyzed as they arrive and
  wait for your review on the dashboard;
  `webhook.redo_ocr`/`webhook.apply_policy` on the same tab pick the
  hands-off pipeline of your choice.

## Where next

- An archive full of years-old scans? Work it in deliberate passes:
  [Cleaning up an existing archive](usage/corpus.md).
- What sessions, gates and proposals actually are:
  [Analysis sessions](usage/sessions.md) and
  [Proposals & the journal](usage/proposals.md).
- Every knob, with defaults: [Configuration](configuration.md).
