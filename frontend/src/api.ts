// Typed API client for the paperless-llm backend.

export type ProposalStatus =
  | "draft"
  | "pending"
  | "approved"
  | "rejected"
  | "applied"
  | "superseded"
  | "no_change";

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
  timings: (CallTiming & { pages?: string })[];
}

export interface EntityRef {
  id: number;
  name: string;
  document_count?: number | null;
  match?: string;
  matching_algorithm?: number;
  is_inbox_tag?: boolean;
}

export interface MergeCandidate {
  entity_type: string;
  source: { id: number; name: string; document_count: number | null };
  target: { id: number; name: string; document_count: number | null };
  string_score: number;
  semantic_score: number | null;
}

export interface Job {
  id: number;
  kind: string;
  params: Record<string, unknown>;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  total: number;
  done: number;
  failed: number;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface JobDetail extends Job {
  sessions: Session[];
}

export interface JobCreate {
  document_ids?: number[];
  query?: string;
  inbox?: boolean;
  untagged_only?: boolean;
  redo_ocr?: boolean;
  apply_policy?: "review" | "auto";
  instructions?: string;
}

export interface Stats {
  pending_proposals: number;
  active_sessions: number;
  queue_pending: Record<string, number>;
  active_jobs: number;
}

export interface CallTiming {
  started_at: string;
  finished_at: string;
  duration_s: number;
  ttft_s: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  tps: number | null;
}

export interface TranscriptItem {
  role: "user" | "agent" | "tool";
  content: string;
  origin: "chat" | "pipeline";
  tool_name: string | null;
  tool_args: Record<string, unknown> | null;
  tool_result: string | null;
  timing: CallTiming | null;
}

export interface AttemptRecord {
  attempt: number;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface RetryInfo {
  state: string;
  attempts: number;
  max_attempts: number;
  next_attempt_at: string | null;
  history: AttemptRecord[];
}

export interface SessionDetail extends Session {
  transcript: TranscriptItem[];
  proposals: Proposal[];
  retry: RetryInfo | null;
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
  analyzeEntity: (entityType: string, id: number, instructions?: string) =>
    request<Session>(`/api/sessions/analyze/${entityType}/${id}`, {
      method: "POST",
      body: JSON.stringify({ instructions: instructions || null }),
    }),
  mergeCandidates: (entityType: string) =>
    request<MergeCandidate[]>(`/api/entities/${entityType}/merge-candidates`),

  listJobs: () => request<Job[]>("/api/jobs"),
  getJob: (id: number) => request<JobDetail>(`/api/jobs/${id}`),
  createJob: (body: JobCreate) =>
    request<Job>("/api/jobs", { method: "POST", body: JSON.stringify(body) }),
  cancelJob: (id: number) =>
    request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  getStats: () => request<Stats>("/api/stats"),
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
  retrySession: (sessionId: number) =>
    request<Session>(`/api/sessions/${sessionId}/retry`, { method: "POST" }),

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
