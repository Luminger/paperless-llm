# Analysis sessions

A **session** is one conversation between the agent and one thing in
your archive — a document, a tag, a correspondent, or a document type.
Its timeline is a list of **steps**: OCR runs, analysis turns, chat
turns. Everything the agent did is on the record: reasoning, every tool
call with its full result, per-call timings.

## The pipeline

A document analysis runs through phases:

```
queued → [ocr_running → ocr_review] → analyzing → done
```

The OCR stage only exists when you request re-OCR (per analysis, or as
the webhook default). In an [OCR-only job](jobs.md#two-kinds-of-document-jobs)
the pipeline deliberately ends at the gate — resolved gate, session
done, no analysis.

## The OCR gate

Re-OCR **stops the pipeline** until you decide. The gate shows a
side-by-side (or unified) diff of the current paperless content vs. the
fresh transcription:

- **Accept** — the new text is written to paperless (as a journaled,
  revertible change) and analysis proceeds on it.
- **Edit** — fix the text in place first; your handiwork is what gets
  written.
- **Keep existing** — discard the transcription, analyze the current
  content.
- **Re-run with instructions** — redo the OCR step with extra guidance
  ("the stamps matter, transcribe them").

Nothing downstream sees unreviewed OCR output.

### Born-digital documents

Not every PDF is a scan. Pages with a real, visible embedded text
layer are read directly from the PDF — no vision model involved, no
waiting. Previously-OCRed scans (invisible text over a page image,
e.g. paperless's own tesseract layer) still get the full VLM
treatment: that stale layer is exactly what re-OCR is meant to
replace.

When **every** page was born-digital and the extracted text matches
the stored content, the gate resolves itself (shown as *born-digital —
embedded text verified*): there is no OCR decision for a human to
make, and bulk OCR jobs flow straight through such documents. If a
document was misclassified, the gate's re-run fold offers *“Ignore the
PDF's embedded text”* to force the vision model over every page.
Tunables: `llm.ocr.native_text` (the gate itself) and
`llm.ocr.native_auto_accept_similarity` (auto-resolve threshold,
unset to always gate).

## The decision loop

The agent proposes **one change per turn** — the most foundational
first (create the missing correspondent *before* the metadata update
that references it). When you decide on a proposal, the session
continues automatically:

- **Apply** → the agent is told its proposal was accepted (including
  any edits you made — your values override its own) and proposes the
  next single change, or finishes with a summary.
- **Revise** → tell the agent what to change; a new revision supersedes
  the old proposal.
- Leave it pending, or archive the session — nothing happens without
  you.

## Steering

Free-text steering appears wherever a decision is pending:

- **On a proposal**: "ask the agent to revise" — e.g. *"Use German
  titles"*. The revised proposal supersedes the original.
- **At the end of the feed**: continue the conversation — e.g. *"also
  check whether the date matches the letterhead"*.
- **Per entity**: [instructions](taxonomy.md#per-entity-instructions)
  the agent must obey whenever it sees that entity.

## Reviewing a whole job

When a bulk job leaves dozens of sessions waiting on you, don't walk
them from the dashboard one by one. The job page's **Review N
waiting** button opens the first session that needs you, with a slim
**flow bar** on top: the job it belongs to, how many sessions still
wait, and **Next →**. Next jumps to the following session with an open
gate or pending proposal, wrapping around until nothing waits.

Advancing is deliberately manual: deciding a proposal often makes the
*same* document continue with a follow-up proposal a few seconds later
— you leave when this document is truly done, not when a timer fires.

## Steps can fail — and recover

OCR steps show live progress while they run: which pages are batched
into each model call, the text each batch returned, and the same call
metrics agent turns show (duration, tokens, tokens/second). Flipped or
sideways scans are detected (tesseract orientation detection) and
rotated upright before the vision model reads them — the step notes
which pages were auto-rotated.

Every step carries its attempt history. Failed steps auto-retry with
backoff (configurable); **Retry now** skips the wait and revives steps
whose budget ran out. While a session has queued or running work, a
**Stop** button (session header and session lists) aborts the
in-flight model call and cancels pending steps — fully recoverable,
Retry runs a stopped step again. Every LLM call is also capped by a
wall-clock **max execution time** (Settings → Models, per profile), so
a stuck endpoint fails the step — into the normal retry machinery —
instead of hanging it forever. **Redo** re-runs any step with optionally amended
input — everything after it is superseded, because it was built on
state the redo invalidates. Open proposals of superseded steps are
superseded too; applied ones are history and stay untouched (revert
them from the journal if needed).

## Archiving

Archiving a session takes it off the active lists and refuses new work
and applies — but its journal stays: applied changes remain revertible.
Going back in time is always allowed.

## The document panel

Judging a proposal (or an OCR result) against nothing but field values
is reviewing blind. The **Document** button in a session's header pins
the document beside the timeline: **Pages** shows one page at a time
with pagination and zoom (up to 400%, switching to a sharper render
past 150% — pan by scrolling while zoomed), **Text** the stored OCR
content. The panel is a right-hand sidebar
(off-canvas collapsible) — the page scrolls, the document stays, and
its content scrolls independently. Collapse it and a slim rail on the
viewport edge (or Ctrl/⌘+B) brings it back; closed it costs no width.
A button in the panel's header moves it to the other side of the
screen — the choice is saved to your server-side preferences, so every
browser agrees.
On phones it opens as a sheet instead. Its state lives in the
URL (`?doc=pages`), so a review link can arrive with the evidence
already open. The OCR gate offers the same panel inline
("Compare against the pages") — the one review where seeing the
document is non-negotiable.
