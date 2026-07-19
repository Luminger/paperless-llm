// THE proposal-kind registry (AUDIT FS-C1). Kind knowledge — which
// kinds are internal plumbing, which get the structured document
// editor, how they're labeled — lives here, not in string comparisons
// scattered across StepCard/ProposalCard/EntityPage.

export { proposalKindLabel } from "./proposal-payload";

/** Internal kinds exist for pipeline plumbing (the OCR gate's
 * journaled content write). They never render as proposal cards, never
 * count in badges, and the decision loop skips them. */
const INTERNAL_KINDS = new Set(["replace_content"]);

export function isInternalKind(kind: string): boolean {
  return INTERNAL_KINDS.has(kind);
}

/** Kinds edited with the structured document-metadata editor. */
export function hasDocumentEditor(kind: string): boolean {
  return kind === "update_document_metadata";
}

/** Taxonomy kinds edited with the structured entity editor (named
 * fields, typed widgets — same standard as the document editor).
 * Anything not in either registry falls back to the generic field
 * editor, which exists only as a safety net for future kinds. */
const ENTITY_EDITOR_KINDS = new Set([
  "create_entity",
  "update_entity",
  "merge_entities",
  "delete_entity",
]);

export function hasEntityEditor(kind: string): boolean {
  return ENTITY_EDITOR_KINDS.has(kind);
}
