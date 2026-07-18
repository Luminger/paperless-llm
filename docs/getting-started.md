# Getting started

## What you need

| Piece | Notes |
| --- | --- |
| **paperless-ngx** | Any recent version; you need its URL and an API token (paperless → *Settings → My profile*). |
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
git clone https://github.com/dhs/paperless-llm
cd paperless-llm/deploy/production
cp .env.example .env
$EDITOR .env       # paperless URL + token, LLM endpoint + model
podman compose up -d
```

Open `http://your-host:8100`. The dashboard shows sessions that need
you; the Documents page is where you start your first analysis.

The app keeps all of its state (SQLite database, OCR cache) in the
`appdata` volume. Your paperless data is never touched except through
paperless's own API, and only when you apply a proposal.

## First analysis

1. **Documents** → pick a document → **Analyze**.
2. If you checked *re-run OCR*, review the content diff at the gate.
3. Watch the agent work — reasoning and tool calls stream live.
4. A proposal card appears. Edit any field if you like, then **Apply**.
5. The session continues by itself until the document is done.

## Hooking up the inbox

Two options, freely combined:

- **Bulk job**: *Documents → Select… → Analyze* or the inbox/untagged
  scopes on the Jobs page.
- **Webhook**: set `PLLM_WEBHOOK_SECRET`, then add a paperless
  *workflow* (trigger: document added → action: webhook) POSTing to
  `http://your-host:8100/api/webhooks/paperless` with the header
  `X-PLLM-Token: <secret>`. New documents get analyzed as they arrive
  and wait for your review on the dashboard.

## Signing in

You sign in with your **paperless credentials** — paperless is the user
store, there is nothing to configure. Applied changes run under your
own paperless token, so paperless's audit trail names you. Details in
[Configuration → Authentication](configuration.md#authentication).
