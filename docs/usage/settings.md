# Settings & prompts

Settings open as a modal from the user menu (or `/settings`). Sections
are deep-linkable: `/settings#preferences`, `/settings#prompts`,
`/settings#system`.

## Date & time

Date format (ISO / European / US), time format (24h, with seconds,
12h), and timezone (system or a fixed IANA zone). Stored **on the
server** — every browser and device shows the same formats. Timestamps
themselves are always stored in UTC; these preferences only control
rendering. The agent is told your formats too, so dates in its prose
match what you see (machine-readable fields stay ISO).

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

## System

A read-only view of the effective server configuration: model profiles,
paperless connection, queue and retry settings, webhook status, auth
mode. Secrets never leave the server — only their presence is shown.

## Theme

Light, dark, or follow-the-system — in the user menu, stored per
browser.
