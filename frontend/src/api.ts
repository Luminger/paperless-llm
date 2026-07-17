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
  base_snapshot: Record<string, unknown> | null;
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
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  proposal_count: number;
}

export interface SessionPage {
  count: number;
  page: number;
  page_size: number;
  results: Session[];
}

export interface SessionFilter {
  entity_type?: string;
  entity_id?: number;
  archived?: boolean;
  unfinished?: boolean;
  page?: number;
  page_size?: number;
}

export interface Meta {
  paperless_url: string;
}

export interface ResourceFetchStatus {
  in_flight: number;
  last_fetched_at: string | null;
  last_error: string | null;
}

export interface SyncStatus {
  resources: Record<string, ResourceFetchStatus>;
}

export interface AuditEntry {
  id: number;
  ts: string;
  kind: string;
  action: string;
  actor: string;
  detail: Record<string, unknown>;
}

export interface AuditPage {
  count: number;
  page: number;
  page_size: number;
  results: AuditEntry[];
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
  // App-local agent instructions (binding for the agent).
  instructions?: string;
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
  lifetime: Record<string, number>;
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
  ts: string | null;
}

export interface AttemptRecord {
  attempt?: number;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  manual_retry_at?: string;
}

export type StepKind = "ocr" | "analysis" | "chat";
export type StepState =
  | "pending"
  | "running"
  | "awaiting_user"
  | "succeeded"
  | "failed"
  | "superseded"
  | "cancelled";

export interface Step {
  id: number;
  session_id: number;
  kind: StepKind;
  state: StepState;
  lane: string;
  input: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string | null;
  attempts: AttemptRecord[];
  attempt_count: number;
  max_attempts: number;
  scheduled_at: string | null;
  supersedes_id: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  transcript: TranscriptItem[];
}

export interface SessionDetail extends Session {
  steps: Step[];
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
  revertCheck: (id: number) =>
    request<{ revert_noop: boolean }>(`/api/proposals/${id}/revert-check`),

  getMeta: () => request<Meta>("/api/meta"),
  getSyncStatus: () => request<SyncStatus>("/api/sync/status"),
  listAudit: (page = 1, pageSize = 20, kind?: string) =>
    request<AuditPage>(
      `/api/audit?page=${page}&page_size=${pageSize}${kind ? `&kind=${kind}` : ""}`,
    ),
  listSessions: (filter: SessionFilter = {}) => {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(filter)) {
      if (v !== undefined) params.set(k, String(v));
    }
    const qs = params.toString();
    return request<SessionPage>(`/api/sessions${qs ? `?${qs}` : ""}`);
  },
  archiveSession: (id: number) =>
    request<Session>(`/api/sessions/${id}/archive`, { method: "POST" }),
  unarchiveSession: (id: number) =>
    request<Session>(`/api/sessions/${id}/unarchive`, { method: "POST" }),
  getEntity: (entityType: string, id: number) =>
    request<EntityRef>(`/api/entities/${entityType}/${id}`),
  setInstructions: (entityType: string, id: number, instructions: string) =>
    request<{ instructions: string }>(`/api/entities/${entityType}/${id}/instructions`, {
      method: "PUT",
      body: JSON.stringify({ instructions }),
    }),
  getSession: (id: number) => request<SessionDetail>(`/api/sessions/${id}`),
  sendMessage: (id: number, content: string) =>
    request<Step>(`/api/sessions/${id}/messages`, {
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
  resolveStep: (sessionId: number, stepId: number, content: string | null) =>
    request<Step>(`/api/sessions/${sessionId}/steps/${stepId}/resolve`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  retryStep: (sessionId: number, stepId: number) =>
    request<Step>(`/api/sessions/${sessionId}/steps/${stepId}/retry`, {
      method: "POST",
    }),
  redoStep: (sessionId: number, stepId: number, input?: Record<string, unknown>) =>
    request<Step>(`/api/sessions/${sessionId}/steps/${stepId}/redo`, {
      method: "POST",
      body: JSON.stringify({ input: input ?? null }),
    }),

  listDocuments: (
    opts: {
      query?: string;
      tag_id?: number;
      correspondent_id?: number;
      document_type_id?: number;
      page?: number;
    } = {},
  ) => {
    const params = new URLSearchParams({ page: String(opts.page ?? 1) });
    if (opts.query) params.set("query", opts.query);
    if (opts.tag_id) params.set("tag_id", String(opts.tag_id));
    if (opts.correspondent_id) params.set("correspondent_id", String(opts.correspondent_id));
    if (opts.document_type_id) params.set("document_type_id", String(opts.document_type_id));
    return request<{ count: number; all?: number[]; results: PaperlessDocument[] }>(
      `/api/entities/documents?${params}`,
    );
  },
  getDocument: (id: number) =>
    request<PaperlessDocument>(`/api/entities/documents/${id}`),
  listTags: () => request<EntityRef[]>("/api/entities/tags"),
  listCorrespondents: () => request<EntityRef[]>("/api/entities/correspondents"),
  listDocumentTypes: () => request<EntityRef[]>("/api/entities/document_types"),
  listStoragePaths: () => request<EntityRef[]>("/api/entities/storage_paths"),
};
