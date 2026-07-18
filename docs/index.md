# paperless-llm

**A local-LLM assistant that keeps your [paperless-ngx](https://docs.paperless-ngx.com/)
archive tidy — without a single byte of your documents leaving your
network.**

paperless-llm connects your paperless instance to a language model *you*
host (vLLM, llama.cpp, LM Studio — anything OpenAI-compatible) and puts
an agent to work on the boring parts of document management:

- **OCR that's actually good** — a vision model re-reads scans page by
  page and produces clean Markdown, replacing the garbled Tesseract
  layer. You review the diff before anything is written.
- **Metadata proposals** — titles, correspondents, document types,
  tags, dates, storage paths. The agent reads the document, checks the
  existing taxonomy, and proposes changes — *one at a time*, for you to
  apply, edit, or ignore.
- **Taxonomy governance** — duplicate-tag detection, merge proposals,
  naming consistency, per-entity instructions the agent must obey
  ("this tag is only for tax documents").
- **Bulk jobs & automation** — analyze the whole inbox, everything
  untagged, or a tag's worth of documents; optionally auto-apply with a
  full undo journal. A webhook analyzes new documents as paperless
  consumes them.

## The two ideas that shape everything

**Privacy by construction.** There is exactly one kind of LLM endpoint
in the entire codebase: a local, OpenAI-compatible one. There is no
cloud provider integration to misconfigure — point the config at
machines on your network and your documents *cannot* leave it.

**Human in the loop, journal underneath.** The agent never writes to
paperless on its own authority. It emits *proposals*; you apply them
(or let a bulk job auto-apply them). Every applied change is journaled
with before/after snapshots and can be reverted — going back in time is
always allowed.

## How a session feels

1. Pick a document (or let the inbox webhook do it) — an **analysis
   session** starts.
2. If re-OCR is requested, the pipeline stops at the **OCR gate**: a
   side-by-side diff of the old and new text. Accept, hand-fix, or keep
   the existing content — nothing proceeds until you decide.
3. The agent reads the content, inspects your taxonomy, and proposes
   the **single most foundational change** — say, creating the
   correspondent that doesn't exist yet.
4. You apply it (possibly edited). The session **continues on its
   own**: the agent is told what you decided and proposes the next
   change, until the document is in shape and it writes a short summary.
5. Everything — reasoning, tool calls, decisions, timings — stays
   visible in the session transcript.

## Where to go next

- [Getting started](getting-started.md) — run it with podman/docker
  compose in a few minutes.
- [Configuration](configuration.md) — model profiles, auth modes,
  webhook, tuning knobs.
- [Architecture](architecture.md) — how the step engine, proposals, and
  the journal fit together.
