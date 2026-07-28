// THE status-tone and label maps (AUDIT UI-U1 / FP-C7). Components use
// semantic tone classes built from the CSS tokens in index.css — no
// raw palette utilities, no hand-paired dark: variants anywhere else.

export type Tone =
  | "success"
  | "warning"
  | "info"
  | "notice"
  | "special"
  | "destructive"
  | "muted";

/** Badge/chip surface: tinted background + toned text. The token value
 * flips per theme, so no dark: pair is ever needed. */
export const TONE_BADGE: Record<Tone, string> = {
  success: "bg-success/15 text-success",
  warning: "bg-warning/15 text-warning",
  info: "bg-info/15 text-info",
  notice: "bg-notice/15 text-notice",
  special: "bg-special/15 text-special",
  destructive: "bg-destructive/15 text-destructive",
  muted: "bg-muted text-muted-foreground",
};

/** Session/step/job/proposal status → tone. One map for every badge. */
export const STATUS_TONE: Record<string, Tone> = {
  draft: "muted",
  no_change: "notice",
  pending: "info",
  applied: "success",
  superseded: "muted",
  queued: "muted",
  running: "info",
  awaiting_user: "warning",
  completed: "success",
  succeeded: "success",
  cancelled: "muted",
  failed: "destructive",
  idle: "success",
  paused: "warning",
  stopped: "muted",
};

/** Audit-log kind → tone. */
export const AUDIT_KIND_TONE: Record<string, Tone> = {
  proposal: "success",
  task: "special",
  job: "special",
  auth: "warning",
  webhook: "info",
  paperless: "notice",
  session: "muted",
};

import type { Job } from "../api";

/** Human name of a job's scope, e.g. "Inbox" or "3 selected
 * documents". Jobs carry a label from creation; older ones fall back
 * to their scope facts. Shared by the jobs list, the job page and the
 * session flow bar. */
export function scopeLabel(job: Job): string {
  const p = job.params;
  if (typeof p.label === "string" && p.label) return p.label;
  if (p.inbox) return "Inbox";
  if (p.untagged_only) return "Untagged documents";
  if (Array.isArray(p.document_ids)) return `${p.document_ids.length} selected documents`;
  return job.kind.replaceAll("_", " ");
}

/** "user:simon" → {label: "simon", user: true}; anything else is the
 * automatic pipeline. Shared by the audit log and change history. */
export function parseActor(actor: string | null | undefined): {
  label: string;
  user: boolean;
} {
  if (actor && actor.startsWith("user:")) {
    return { label: actor.slice(5), user: true };
  }
  return { label: "automatic", user: false };
}
