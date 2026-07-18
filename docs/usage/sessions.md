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
the webhook default).

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

## Steps can fail — and recover

Every step carries its attempt history. Failed steps auto-retry with
backoff (configurable); **Retry now** skips the wait and revives steps
whose budget ran out. **Redo** re-runs any step with optionally amended
input — everything after it is superseded, because it was built on
state the redo invalidates. Open proposals of superseded steps are
superseded too; applied ones are history and stay untouched (revert
them from the journal if needed).

## Archiving

Archiving a session takes it off the active lists and refuses new work
and applies — but its journal stays: applied changes remain revertible.
Going back in time is always allowed.
