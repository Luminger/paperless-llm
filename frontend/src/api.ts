// Typed API client for the paperless-llm backend.
//
// All request/response types are GENERATED from the backend's OpenAPI
// schema (npm run gen:api → src/api/schema.gen.ts). This file only
// aliases them and provides the thin fetch wrapper — never hand-write
// a type the backend already defines.

import type { components } from "./api/schema.gen";
import { ApiError } from "./lib/errors";

type S = components["schemas"];

export type Proposal = S["ProposalOut"];
export type ProposalStatus = Proposal["status"];
export type Session = S["SessionOut"];
export type SessionDetail = S["SessionDetailOut"];
export type SessionPage = S["SessionPage"];
export type Step = S["StepOut"];
export type StepKind = Step["kind"];
export type StepState = Step["state"];
export type TranscriptItem = S["TranscriptItem"];
export type CallTiming = S["CallTiming"];
export type AttemptRecord = S["AttemptRecord"];
export type Job = S["JobOut"];
export type JobDetail = S["JobDetailOut"];
// Request body: every field has a server-side default.
export type JobCreate = Partial<S["JobCreate"]>;
export type JobPage = S["JobPage"];
export type ProposalPage = S["ProposalPage"];
export type Stats = S["StatsOut"];
export type EntityRef = S["EntityOut"];
export type PaperlessDocument = S["DocumentOut"];
export type DocumentSearchPage = S["DocumentSearchPage"];
export type MergeCandidate = S["MergeCandidateOut"];
export type OcrReview = S["OcrReviewOut"];
export type AuditEntry = S["AuditEntryOut"];
export type AuditPage = S["AuditPage"];
export type Meta = S["MetaOut"];
export type SyncStatus = S["SyncStatusOut"];
export type RevertCheck = S["RevertCheckOut"];
export type SettingsOverview = S["SettingsOut"];
export type Prefs = S["PrefsOut"];
export type AuthMe = S["AuthMeOut"];
export type PrefsUpdate = S["PrefsUpdate"];

export interface SessionFilter {
  entity_type?: string;
  entity_id?: number;
  archived?: boolean;
  unfinished?: boolean;
  page?: number;
  page_size?: number;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    let code = "error";
    let message = `request failed (${resp.status})`;
    try {
      const detail = (await resp.json())?.detail;
      if (detail && typeof detail === "object") {
        code = detail.code ?? code;
        message = detail.message || message;
      } else if (typeof detail === "string") {
        message = detail;
      }
    } catch {
      // non-JSON body — keep the generic message
    }
    if (resp.status === 401 && !url.startsWith("/api/auth/")) {
      // The auth shell re-checks /api/auth/me and shows the login page.
      window.dispatchEvent(new Event("pllm:unauthorized"));
    }
    throw new ApiError(resp.status, code, message);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  listProposals: (status?: ProposalStatus) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    const qs = params.toString();
    return request<ProposalPage>(`/api/proposals${qs ? `?${qs}` : ""}`);
  },
  getProposal: (id: number) => request<Proposal>(`/api/proposals/${id}`),
  patchProposal: (id: number, user_payload: S["ProposalPatch"]["user_payload"]) =>
    request<Proposal>(`/api/proposals/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ user_payload }),
    }),
  proposalAction: (id: number, action: "apply" | "revert") =>
    request<Proposal>(`/api/proposals/${id}/${action}`, { method: "POST" }),
  revertCheck: (id: number) =>
    request<RevertCheck>(`/api/proposals/${id}/revert-check`),

  getMeta: () => request<Meta>("/api/meta"),
  getAuthMe: () => request<AuthMe>("/api/auth/me"),
  login: (username: string, password: string) =>
    request<AuthMe>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<AuthMe>("/api/auth/logout", { method: "POST" }),
  getSettingsOverview: () => request<SettingsOverview>("/api/settings"),
  getPrefs: () => request<Prefs>("/api/prefs"),
  putPrefs: (body: PrefsUpdate) =>
    request<Prefs>("/api/prefs", { method: "PUT", body: JSON.stringify(body) }),
  getSyncStatus: () => request<SyncStatus>("/api/sync/status"),
  listAudit: (page = 1, pageSize = 20, kind?: string) => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (kind) params.set("kind", kind);
    return request<AuditPage>(`/api/audit?${params}`);
  },
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
    request<S["InstructionsOut"]>(
      `/api/entities/${entityType}/${id}/instructions`,
      { method: "PUT", body: JSON.stringify({ instructions }) },
    ),
  getSession: (id: number) => request<SessionDetail>(`/api/sessions/${id}`),
  sendMessage: (id: number, content: S["MessageRequest"]["content"]) =>
    request<Step>(`/api/sessions/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  analyzeDocument: (docId: number, opts: S["AnalyzeRequest"]) =>
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

  listJobs: (page = 1, pageSize = 25) => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    return request<JobPage>(`/api/jobs?${params}`);
  },
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
      page_size?: number;
    } = {},
  ) => {
    const params = new URLSearchParams({ page: String(opts.page ?? 1) });
    if (opts.page_size) params.set("page_size", String(opts.page_size));
    if (opts.query) params.set("query", opts.query);
    if (opts.tag_id) params.set("tag_id", String(opts.tag_id));
    if (opts.correspondent_id) params.set("correspondent_id", String(opts.correspondent_id));
    if (opts.document_type_id) params.set("document_type_id", String(opts.document_type_id));
    return request<DocumentSearchPage>(`/api/entities/documents?${params}`);
  },
  getDocument: (id: number) =>
    request<PaperlessDocument>(`/api/entities/documents/${id}`),
  listTags: () => request<EntityRef[]>("/api/entities/tags"),
  listCorrespondents: () => request<EntityRef[]>("/api/entities/correspondents"),
  listDocumentTypes: () => request<EntityRef[]>("/api/entities/document_types"),
  listStoragePaths: () => request<EntityRef[]>("/api/entities/storage_paths"),
};
