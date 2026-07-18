# Proposals & the journal

A **proposal** is a typed, reviewable change. It is the *only* way
anything the agent wants ends up in paperless.

## Kinds

| Kind | Example |
| --- | --- |
| `update_document_metadata` | Title, correspondent, type, storage path, tags, created date, ASN — any subset in one proposal |
| `replace_content` | The OCR gate's accepted text |
| `create_entity` | A new tag/correspondent/type the document needs |
| `update_entity` | Rename, matching-rule change |
| `merge_entities` | Fold a duplicate into its canonical twin (documents are re-assigned, the source is deleted) |
| `delete_entity` | Remove an unused entity |

## The review card

Each proposal renders as an editable card: the field values paperless
has *right now* (as the agent saw them) next to the proposed values,
with entity ids resolved to names. Edit any field before applying —
the agent's original payload is preserved separately, so you always see
what was yours and what was the model's.

## Applying — with three safety nets

1. **No-op detection** — if paperless already matches the proposed
   state (another session got there first, or you did it by hand), the
   proposal is marked *no change needed*; nothing is written, nothing
   journaled.
2. **Staleness check** — paperless has no revision system, so the app
   verifies value-by-value that the fields the agent *looked at*
   haven't changed since. If they have, the apply is refused and you
   re-review.
3. **The journal** — every applied change stores before/after
   snapshots. **Revert** restores the before-state (with its own
   staleness check, and a preview of exactly what would be restored).

## Revisions

Asking the agent to revise (or the agent revising itself after your
feedback) creates a new **revision** that supersedes the previous
proposal — the chain stays visible. There is no "reject": unwanted
proposals are revised, left pending, or their session archived.

## Where proposals live

Proposals are reviewed **on their session's timeline**, in the context
of the reasoning that produced them. The dashboard counts what awaits
review; deep links to a proposal resolve to its session.

## Per-document history

Every document's detail page carries its **change history**: each
applied change with the fields it touched, **who** applied it (the
signed-in user by name, or *automatic* for auto-applied jobs), when,
whether it was edited before applying or reverted since — and a link
to the session that produced it.
