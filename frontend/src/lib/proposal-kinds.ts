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

/** Kinds edited with the structured document-metadata editor; all
 * others fall back to the generic field editor. */
export function hasDocumentEditor(kind: string): boolean {
  return kind === "update_document_metadata";
}
