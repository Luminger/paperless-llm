# Taxonomy governance

Tags, correspondents, document types, and storage paths tend to rot:
duplicates ("Insurance" vs "Versicherung"), inconsistent naming, dead
entries. The Taxonomy pages put the agent to work on exactly that.

## Entity review sessions

Analyze any entity (or a multi-selection — one job, one session per
entity). The agent inspects the entity, its documents, and its
neighbors, then proposes renames, merges, or deletions — one per turn,
through the same [proposal machinery](proposals.md) as everything else.

## Merge candidates

A deterministic pre-pass lists likely duplicates per taxonomy type:
pairs whose names are close by string distance or — with an embeddings
endpoint configured — by semantic similarity ("Invoice"/"Rechnung").
One click sends a candidate pair to the agent for adjudication; it
verifies against actual usage before proposing a merge.

Merges are proposals like any other: documents are re-assigned to the
surviving entity, the duplicate is deleted, and the journal knows how
to put everything back.

## Matching rules

Every paperless entity carries a [matching rule](https://docs.paperless-ngx.com/advanced_usage/#matching)
(`match` + algorithm + case sensitivity) that drives paperless's *own*
automatic assignment on ingest. paperless-llm keeps this machinery
alive rather than replacing it:

- Entities the agent creates default to **auto (ML)** matching — every
  apply/edit decision you make trains paperless's classifier.
- When a document shows a reliable marker (sender name, IBAN,
  letterhead), the agent may propose an explicit word/exact rule
  instead — those fire deterministically on ingest.
- Reviewing an entity can yield a *fix the matching rule* proposal like
  any other change, and rule edits show up in the proposal diff.
- Invalid combinations are rejected before they become proposals: the
  pattern algorithms (any/all/exact/regex/fuzzy) require a pattern,
  auto and none must not have one.

Entity pages and the taxonomy lists show each entity's rule ("any word
· “kraxi”", "auto (ML)").

## Per-entity instructions

Every entity has an **instructions** field — your standing orders,
injected into the agent's context whenever it deals with that entity,
and *binding*:

> *Tag "Steuern": only for documents relevant to a tax return, not for
> every invoice.*

> *Correspondent "Finanzamt": never merge with other authorities.*

Inbox-type tags come with a seeded default instruction (the agent
removes the inbox tag once a document is fully processed — the tag is a
workflow marker, not metadata). Clearing an instruction is remembered;
defaults never resurrect themselves.

## The inbox tag is special

Inbox tags are workflow state, not taxonomy — the UI refuses to
"analyze" them, and the agent knows to *remove* them from processed
documents rather than treat them as meaningful labels.
