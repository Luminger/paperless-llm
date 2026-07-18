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

/** Plain toned text (icons, inline hints). */
export const TONE_TEXT: Record<Tone, string> = {
  success: "text-success",
  warning: "text-warning",
  info: "text-info",
  notice: "text-notice",
  special: "text-special",
  destructive: "text-destructive",
  muted: "text-muted-foreground",
};

/** Soft panel surface (callouts, edited-value highlights). */
export const TONE_PANEL: Record<Tone, string> = {
  success: "bg-success/10",
  warning: "bg-warning/10",
  info: "bg-info/10",
  notice: "bg-notice/10",
  special: "bg-special/10",
  destructive: "bg-destructive/10",
  muted: "bg-muted/40",
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
