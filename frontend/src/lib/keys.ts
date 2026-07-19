// Central query-key registry. Every useQuery/invalidateQueries goes
// through these — ad-hoc string keys are a bug.

export const keys = {
  sessions: (filter?: object) =>
    filter ? (["sessions", filter] as const) : (["sessions"] as const),
  session: (id: number) => ["session", id] as const,
  // Step-scoped (AUDIT FS-1): after a re-run the NEW gate must never
  // seed from the OLD step's cached payload. The SSE invalidation uses
  // the session prefix and still matches.
  sessionOcr: (id: number, stepId?: number) =>
    stepId == null
      ? (["session-ocr", id] as const)
      : (["session-ocr", id, stepId] as const),
  proposals: () => ["proposals"] as const,
  proposal: (id: number) => ["proposal", id] as const,
  revertCheck: (id: number) => ["revert-check", id] as const,
  documents: (filter?: object, page?: number) =>
    filter !== undefined
      ? (["documents", filter, page] as const)
      : (["documents"] as const),
  document: (id: number) => ["document", id] as const,
  entities: (type: string) => ["entities", type] as const,
  entity: (type: string, id: number) => ["entity", type, id] as const,
  customFields: () => ["custom_fields"] as const,
  mergeCandidates: (type: string) => ["merge-candidates", type] as const,
  jobs: (page?: number, pageSize?: number) =>
    page !== undefined
      ? (["jobs", page, pageSize ?? 25] as const)
      : (["jobs"] as const),
  job: (id: number) => ["job", id] as const,
  stats: () => ["stats"] as const,
  corpus: () => ["corpus"] as const,
  inbox: () => ["inbox"] as const,
  jobAttention: (jobId: number, after?: number) =>
    ["jobs", jobId, "attention", after ?? null] as const,
  audit: (filter?: string, page?: number) =>
    filter !== undefined ? (["audit", filter, page] as const) : (["audit"] as const),
  syncStatus: () => ["sync-status"] as const,
  meta: () => ["meta"] as const,
  auth: () => ["auth"] as const,
  settings: () => ["settings"] as const,
  config: () => ["settings", "config"] as const,
  webhookStatus: () => ["settings", "webhook"] as const,
  documentHistory: (id: number) => ["document", id, "history"] as const,
  documentPreview: (id: number) => ["document", id, "preview"] as const,
  prefs: () => ["prefs"] as const,
} as const;

import type { QueryClient } from "@tanstack/react-query";

/** Invalidate everything a session mutation can touch. */
export function invalidateSession(qc: QueryClient, sessionId: number) {
  qc.invalidateQueries({ queryKey: keys.session(sessionId) });
  qc.invalidateQueries({ queryKey: keys.sessions() });
  qc.invalidateQueries({ queryKey: keys.stats() });
}

/** Invalidate all entity lists of one taxonomy type (+ documents). */
export function invalidateEntities(qc: QueryClient, type?: string) {
  if (type) qc.invalidateQueries({ queryKey: keys.entities(type) });
  else qc.invalidateQueries({ queryKey: ["entities"] });
  qc.invalidateQueries({ queryKey: keys.documents() });
}

/** Everything an apply/revert of a proposal can touch — proposals,
 * the owning session, lists, stats, documents, and (for taxonomy
 * proposals) the affected entity lists. One helper so no mutation
 * hand-picks a subset and goes stale. */
export function invalidateProposalEffects(
  qc: QueryClient,
  p?: { entity_type?: string | null } | null,
) {
  qc.invalidateQueries({ queryKey: ["proposal"] });
  qc.invalidateQueries({ queryKey: keys.proposals() });
  qc.invalidateQueries({ queryKey: ["session"] });
  qc.invalidateQueries({ queryKey: keys.sessions() });
  qc.invalidateQueries({ queryKey: keys.stats() });
  qc.invalidateQueries({ queryKey: ["document"] });
  qc.invalidateQueries({ queryKey: keys.documents() });
  const t = p?.entity_type;
  if (t && t !== "document") invalidateEntities(qc, t);
}
