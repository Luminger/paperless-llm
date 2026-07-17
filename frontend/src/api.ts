// Typed API client for the paperless-llm backend.

export type ProposalStatus =
  | "draft"
  | "pending"
  | "approved"
  | "rejected"
  | "applied"
  | "superseded";

export interface Proposal {
  id: number;
  session_id: number;
  kind: string;
  revision: number;
  supersedes_id: number | null;
  agent_payload: Record<string, unknown>;
  user_payload: Record<string, unknown> | null;
  status: ProposalStatus;
  entity_type: string | null;
  entity_id: number | null;
  created_at: string;
  updated_at: string;
  applied: boolean;
  reverted: boolean;
}

export interface Session {
  id: number;
  agent_kind: string;
  entity_type: string | null;
  entity_id: number | null;
  title: string;
  status: string;
  phase: string | null;
  params: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
  proposal_count: number;
}

export interface OcrReview {
  document_id: number;
  previous_content: string;
  ocr_text: string;
  pages: number;
}

export interface EntityRef {
  id: number;
  name: string;
  document_count?: number | null;
}

export interface TranscriptItem {
  role: "user" | "agent" | "tool";
  content: string;
  origin: "chat" | "pipeline";
  tool_name: string | null;
  tool_args: Record<string, unknown> | null;
  tool_result: string | null;
}

export interface SessionDetail extends Session {
  transcript: TranscriptItem[];
  proposals: Proposal[];
}

export interface SessionEvent {
  type: string;
  session_id: number;
  [key: string]: unknown;
}

export interface PaperlessDocument {
  id: number;
  title: string;
  correspondent: number | null;
  document_type: number | null;
  storage_path: number | null;
  tags: number[];
  created: string | null;
  added: string | null;
  archive_serial_number: number | null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`${resp.status}: ${body.slice(0, 300)}`);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  listProposals: (status?: ProposalStatus) =>
    request<Proposal[]>(`/api/proposals${status ? `?status=${status}` : ""}`),
  getProposal: (id: number) => request<Proposal>(`/api/proposals/${id}`),
  patchProposal: (id: number, user_payload: Record<string, unknown> | null) =>
    request<Proposal>(`/api/proposals/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ user_payload }),
    }),
  proposalAction: (id: number, action: "approve" | "reject" | "apply" | "revert") =>
    request<Proposal>(`/api/proposals/${id}/${action}`, { method: "POST" }),

  listSessions: () => request<Session[]>("/api/sessions"),
  getSession: (id: number) => request<SessionDetail>(`/api/sessions/${id}`),
  sendMessage: (id: number, content: string) =>
    request<Session>(`/api/sessions/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  analyzeDocument: (docId: number, opts: { redo_ocr: boolean; instructions?: string }) =>
    request<Session>(`/api/sessions/analyze/document/${docId}`, {
      method: "POST",
      body: JSON.stringify(opts),
    }),
  getOcrReview: (sessionId: number) =>
    request<OcrReview>(`/api/sessions/${sessionId}/ocr`),
  resolveOcrGate: (sessionId: number, content: string | null) =>
    request<Session>(`/api/sessions/${sessionId}/ocr/gate`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  rerunOcr: (sessionId: number, instructions: string | null) =>
    request<Session>(`/api/sessions/${sessionId}/ocr/rerun`, {
      method: "POST",
      body: JSON.stringify({ instructions }),
    }),

  listDocuments: (query?: string, page = 1) =>
    request<{ count: number; results: PaperlessDocument[] }>(
      `/api/entities/documents?page=${page}${query ? `&query=${encodeURIComponent(query)}` : ""}`,
    ),
  getDocument: (id: number) =>
    request<PaperlessDocument>(`/api/entities/documents/${id}`),
  listTags: () => request<EntityRef[]>("/api/entities/tags"),
  listCorrespondents: () => request<EntityRef[]>("/api/entities/correspondents"),
  listDocumentTypes: () => request<EntityRef[]>("/api/entities/document_types"),
  listStoragePaths: () => request<EntityRef[]>("/api/entities/storage_paths"),
};
