// Central query-key registry. Every useQuery/invalidateQueries goes
// through these — ad-hoc string keys are a bug.

export const keys = {
  sessions: (filter?: object) =>
    filter ? (["sessions", filter] as const) : (["sessions"] as const),
  session: (id: number) => ["session", id] as const,
  sessionOcr: (id: number) => ["session-ocr", id] as const,
  proposals: () => ["proposals"] as const,
  revertCheck: (id: number) => ["revert-check", id] as const,
  documents: (filter?: object, page?: number) =>
    filter !== undefined
      ? (["documents", filter, page] as const)
      : (["documents"] as const),
  document: (id: number) => ["document", id] as const,
  entities: (type: string) => ["entities", type] as const,
  entity: (type: string, id: number) => ["entity", type, id] as const,
  mergeCandidates: (type: string) => ["merge-candidates", type] as const,
  jobs: () => ["jobs"] as const,
  job: (id: number) => ["job", id] as const,
  stats: () => ["stats"] as const,
  audit: (filter?: string, page?: number) =>
    filter !== undefined ? (["audit", filter, page] as const) : (["audit"] as const),
  syncStatus: () => ["sync-status"] as const,
  meta: () => ["meta"] as const,
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
