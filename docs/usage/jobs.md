# Jobs & automation

Every analysis run is tracked as a **job** — a single manual analysis,
a bulk run over fifty documents, a taxonomy batch, or a webhook ingest.
Jobs give the run a progress bar, a failure count, cancellation, and an
audit-log entry.

## Scopes are deterministic

A bulk job's document set is always an explicit, reproducible scope:

- **Selected documents** — multi-select in the Documents list
  (select-all spans all result pages)
- **Inbox** — everything carrying an inbox tag
- **Untagged** — documents with no tags
- **Tag** — everything with a given tag
- **All documents** — the whole archive (offered for OCR-only jobs)
- **Corpus batch** — the next N never-analyzed documents, oldest
  first (see [Cleaning up an existing archive](corpus.md))
- **Entities** — a set of tags/correspondents/types for taxonomy review

There is deliberately no "documents matching this search text" scope —
a job you re-run should mean the same thing every time. Every scope is
resolved to concrete document ids at creation; the job page shows
exactly what it covers. Documents that already have an active session
are skipped.

## Two kinds of document jobs

- **Analyze metadata** (default) — the full pipeline: optional re-OCR
  (gated), then the metadata analysis with its decision loop.
- **Re-do OCR only** — every document is re-transcribed and the
  pipeline **ends there**: no analysis, no metadata proposals. In
  review mode each document gates for your decision; with auto-apply
  (off by default) the new text is written directly — journaled and
  revertible — and unchanged transcriptions are skipped. This is the
  corpus-rehab opener: run it over **All documents** before starting
  [metadata cleanup](corpus.md).

## Apply policy

- **`review`** (default) — every proposal waits for a human.
- **`auto`** — proposals are applied immediately after each analysis:
  still validated, still journaled, still revertible. The journal is
  the safety net; the policy only skips the waiting. Auto-applied
  sessions continue autonomously through the decision loop, with a
  configurable brake (`queue.auto_continuation_limit`) so a confused
  model can't loop forever.

Failures under `auto` (staleness conflicts, validation) simply leave
the proposal pending for a human instead of failing the job.

## Lanes

Bulk work runs on the **batch** lane, interactive sessions on the
**interactive** lane — your chat turn never waits behind fifty inbox
documents. Concurrency per lane is configurable; the model endpoint's
`max_concurrent` remains the global cap.

## The webhook

With `webhook.secret` set, paperless workflows can POST document ids to
`/api/webhooks/paperless` (header `X-PLLM-Token`). Each ingest becomes
a job like any other — visible, cancellable, audited. Configure
`webhook.redo_ocr` and `webhook.apply_policy` for the hands-off
pipeline of your choice: from "just propose, I'll review over coffee"
to "fix the metadata and let me spot-check the journal".

## Reviewing a job's output

A review-mode job over many documents produces many waiting sessions.
The job page's **Review N waiting** button walks them as a queue — see
[flow-through review](sessions.md#reviewing-a-whole-job).

## Cancellation

Cancelling a job cancels its still-pending steps; running steps finish
(their results remain useful). Sessions re-derive their state from the
cancelled tail — nothing is left dangling.


## Pausing, resuming, bulk retry

**Pause** stops workers from picking up the job's remaining work
(running steps finish); **Continue** resumes. **Retry N failed** runs
every failed or stopped session again — or select rows with the
checkboxes and retry exactly that selection. The job's session list is
paginated, filterable by status, and shows each session's document.
