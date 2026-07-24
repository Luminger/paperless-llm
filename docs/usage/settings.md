# Settings & prompts

Settings open as a modal from the user menu (or `/settings`). Sections
are deep-linkable: `/settings#preferences`, `/settings#models`,
`/settings#prompts`, `/settings#paperless`, `/settings#system`.
Everything is stored on the server — every browser shows the same.
Changing models or prompts requires **administrator rights** (a
paperless superuser account); other sections are for everyone.

## Date & time

The usual picker: a **time zone** (automatic per browser, or a fixed
IANA zone — the list shows GMT offsets like `(GMT+02:00)
Europe/Berlin`), a **date format** and a **time format**, each option
shown with a live example. Deliberately no "system locale" format:
preferences are shared across browsers, so "whatever this device does"
would render differently everywhere. Timestamps are always stored in
UTC and converted for display.

## Models

The runtime-editable slice of the configuration: agent / OCR /
embeddings / reranker endpoints and knobs, the auto-continuation
brake. Every value shows **where it comes from** —
environment (locked), set here, config file, or default — per the
[precedence rules](../configuration.md#precedence). Admin changes
apply immediately, validate before persisting, and can be reset back
to the underlying config-file/default value per field.

Every model card carries a **Test connection** probe, and the two
completion cards an **Autodetect** (admin-only — they spend a few real
tokens):

- **Test connection** — one tiny call through the same code path
  production uses. The OCR profile is tested **with an image
  attached**, so a text-only server that would fail real OCR runs
  fails the test too. Embeddings embed a test string (the result shows
  the vector dimension); the reranker ranks an obvious two-document
  pair (the result shows whether the invoice actually came first).
  Shows latency and the model's reply.
- **Autodetect (agent)** — reads the server's context window from
  whatever metadata it exposes (vLLM `/v1/models` `max_model_len`,
  llama.cpp `/props`, Ollama `/api/show` — preferring the *serving*
  window over the model-card maximum) and fills a suggested input
  clamp (~¾ of the window) into the form.
- **Autodetect (OCR)** — three findings, combined into one suggestion:
  the server's **images-per-request cap** (empirical probe with tiny
  images, binary search up to 16), the **token cost of one page** at
  your configured render DPI (measured by diffing the usage-reported
  prompt tokens of a text-only call vs. one with a blank A4 page — so
  the server's own preprocessor pricing is what's measured), and from
  that plus the context window, **how many pages actually fit one
  request** including ~1k reserved output tokens per page. The
  suggested `max_images_per_request` is the binding constraint —
  whichever of server cap and context fit is smaller.

Detected values are only ever drafts — you review and save them like
any hand-typed change. When a server exposes no metadata or no image
cap, the probe says so instead of guessing.

## Prompts

Four prompts, shown in full — nothing hidden behind folds:

- **Additional agent instructions** — *the* place for your context:
  whose archive this is, language rules, house conventions. Appended to
  every agent prompt.
- **Agent system prompt** — the base prompt, system-supplied and
  usually best left alone. Editable for model-specific tuning; a
  modified prompt is pinned (it no longer receives system updates) and
  gets a *modified* badge plus a one-click **Revert to default**.
- **Additional OCR instructions** — transcription rules ("stamps and
  margin notes matter").
- **OCR system prompt** — same override semantics as the agent one.

Editing the OCR prompt invalidates the OCR cache for exactly the
affected transcriptions — prompt tweaks always take effect.

## Paperless

The instance this app is attached to: a link to it, the API endpoint,
how the app authenticates, TLS verification state (with a loud warning
when disabled). The connection itself is read-only by design —
configured via environment or config file only, so a bad value can
never lock you out of the UI that would fix it.

### Webhook ingress

Status (both sides: secret configured here, workflow present in
paperless — and whether that workflow's **content actually matches the
current settings**: URL, secret header, payload shape, trigger.
Existence is not sync — after changing the public URL or secret the
workflow still posts the old values, and the status says so loudly
until you re-run the setup), the runtime-editable webhook settings
(shared secret, this app's public URL, re-OCR default, apply policy),
and one admin action:

- **Set up automatically** — creates the paperless workflow for you:
  trigger “Document Added” (all sources), webhook action posting
  `{doc_url}` to this app with the secret header. Generates and
  persists a secret first when none is configured. If a workflow
  already posts to this app, it is **healed** instead — URL and secret
  refreshed, your custom name and order kept. Requires
  `webhook.public_url` and a superuser paperless account.

Paperless offers no way to test-fire a workflow, so the proof of the
full path is simply the next consumed document: it shows up as a
`webhook ingested` audit record and a webhook job.

## System

Version, database backend, worker pool and retry policy — read-only
runtime facts. Secrets never leave the server; only their presence is
shown.

## Theme

Light, dark, or follow-the-system — in the user menu, stored per
browser.

## Sessions

Everywhere you are signed in — browser and platform, sign-in time, last
activity. Revoking a session signs that browser out immediately,
server-side (the cookie becomes worthless, not just deleted).
Administrators see and can end every user's sessions; the current
session is never revocable here — sign out instead.
