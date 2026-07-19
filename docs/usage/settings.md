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
brake, webhook defaults. Every value shows **where it comes from** —
environment (locked), set here, config file, or default — per the
[precedence rules](../configuration.md#precedence). Admin changes
apply immediately, validate before persisting, and can be reset back
to the underlying config-file/default value per field.

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
when disabled), and the webhook status. Read-only by design — the
connection is configured via environment or config file only, so a bad
value can never lock you out of the UI that would fix it.

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
