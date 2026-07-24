# Cleaning up an existing archive

A fresh paperless-llm install usually meets an archive that is *not*
fresh: hundreds of inbox documents, years of Tesseract OCR of varying
quality, correspondents that are wrong more often than right. You
cannot review a thousand documents in one sitting — and you shouldn't
try. The app is built around working the backlog **in deliberate
passes**, each one making the next one easier.

## The dashboard is the worklist

Three blocks at the top of the dashboard tell you where the work is:

- **Inbox** — documents carrying an inbox tag that have *no active
  session yet*. One button sends the whole inbox through analysis (the
  dialog offers a **re-do OCR first** flag for scans whose stored text
  is rotten); a **Re-OCR…** button next to it runs the OCR-only
  pipeline over the inbox instead — new text, no metadata analysis.
- **Corpus** — a progress bar: *"118 of 2,400 documents analyzed"* —
  with an **Analyze next batch** button. This is the heart of the
  cleanup workflow, described below.
- **Needs attention** — sessions waiting on *you*: an open OCR gate, a
  proposal to review, a failure worth a look. Each row says why.

## Pass 1 — fix the text: an OCR-only job

Metadata analysis is only as good as the text it reads, so the first
pass over a rotten corpus is usually **Re-do OCR only** (Jobs → New
job, or the inbox card's **Re-OCR…** button for inbox scope): every
document in scope is re-transcribed by the vision model and the
pipeline **stops there** — no analysis follows, no metadata is
touched. The **All documents** scope exists precisely for this job.

- In **review** mode each document parks at the OCR gate; you accept,
  hand-fix, or keep the existing text.
- With **auto-apply** (opt-in, off by default) the new text is written
  directly — journaled and revertible like every other change, and
  documents whose transcription matches the existing content are left
  untouched. For a thousand-document rehab this is the realistic
  setting; spot-check the journal instead of clicking a thousand gates.

OCR results are cached (document + content checksum + model + prompt),
so re-running a job never re-transcribes what has already been read.

## Pass 2 — fix the metadata: corpus batches

You can't schedule a thousand analyses at once and hope; the early
documents are where you and the agent negotiate what the taxonomy
*should* look like. The **Corpus** block turns that into a loop:

1. Pick a batch size (10 / 25 / 50) and press **Analyze next batch**.
2. A review-mode job is created for the next slice of the corpus.
3. Work through it (see [flow-through review](sessions.md#reviewing-a-whole-job)).
4. Press the button again. Repeat until the bar is full.

**How the corpus is walked.** A document counts as **processed** once
it has a *completed metadata analysis* — a session that ran to the end,
with every proposal decided. OCR-only sessions do **not** count (they
fix text, not metadata). Each batch selects the next N documents that
have never been processed, ordered by **created date, oldest first** —
so the walk starts at the beginning of your archive's history and moves
forward in time, deterministically. Pressing the button twice in a row
does not analyze the same documents twice: the first batch's documents
have active sessions, and documents with an active session are skipped
at job creation.

Because the scope is resolved to concrete document ids the moment the
job is created (like every job), a batch is a stable, inspectable set —
its job page shows exactly which documents it covers.

**Why oldest first?** Old documents anchor the recurring patterns:
the correspondents that appear a hundred times, the document types
your archive actually needs. Getting them right early means paperless
itself starts helping (next section).

## The flywheel: matching rules

Every entity the agent creates gets a paperless **matching rule** —
[auto (ML)](https://docs.paperless-ngx.com/advanced_usage/#matching) by
default, or an explicit word/exact rule when the document shows a
reliable marker (sender name, IBAN). Your apply/edit decisions train
paperless's own classifier, and explicit rules fire on ingest.

The consequence for the cleanup: **later batches get easier.** By batch
ten, paperless pre-assigns most correspondents and types correctly, and
the agent's proposals shrink to confirmations. When a whole batch goes
through without an edit, that's the signal you can trust wider
automation:

- switch the corpus batches to a larger size, or
- run remaining scopes with **auto-apply** and audit the journal, or
- let the [webhook](jobs.md#the-webhook) handle everything new while
  you finish the tail.

## Finding things in a big archive

The agent's `find_documents` tool is built for archives of any size:
paperless full-text search recalls candidates across *all* documents,
a local [reranker](../configuration.md#llmreranker-optional) (when
configured) re-orders them by actual relevance, and only the top hits —
compact summaries with short snippets — enter the model's context. The
agent reads full documents only when it decides a hit matters. Context
stays bounded whether your archive holds two hundred documents or
twenty thousand.
