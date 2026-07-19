// The one place a status string becomes a colored label. Colors come
// from the semantic tone tokens (lib/labels.ts) — no raw palette here.

import { Badge } from "@/components/ui/badge";
import { Tip } from "@/components/app/Tip";
import { STATUS_TONE, TONE_BADGE } from "@/lib/labels";
import { cn } from "@/lib/utils";

const labels: Record<string, string> = {
  no_change: "no change needed",
  awaiting_user: "needs input",
};

/* Statuses whose one-word label doesn't explain itself get a hover
 * explanation (UI-U4). The obvious ones (pending, applied…) stay bare. */
const explanations: Record<string, string> = {
  superseded: "Replaced by a newer revision of this proposal — nothing was applied from this one.",
  no_change: "The agent looked and found paperless already correct — nothing to apply.",
  rejected: "Dismissed by a user — nothing was applied.",
};

const overrides: Record<string, string> = {
  // Applied keeps the brand-solid look; superseded gets its strikethrough.
  applied: "bg-primary text-primary-foreground",
  superseded: "line-through",
};

function toneClass(status: string): string {
  if (status === "applied") return overrides.applied;
  return cn(TONE_BADGE[STATUS_TONE[status] ?? "muted"], overrides[status]);
}

export function StatusBadge({ status }: { status: string }) {
  const badge = (
    <Badge
      variant="secondary"
      className={cn("capitalize", toneClass(status))}
      tabIndex={explanations[status] ? 0 : undefined}
    >
      {labels[status] ?? status.replaceAll("_", " ")}
    </Badge>
  );
  const why = explanations[status];
  return why ? <Tip content={why}>{badge}</Tip> : badge;
}

/** Session badge: color follows the STATUS, text shows the PHASE. */
export function SessionStatusBadge({
  status,
  phase,
  error,
}: {
  status: string;
  phase?: string | null;
  /** The session's error text — surfaces as the Error badge's tooltip
   * (the list deliberately shows a calm badge, not error prose). */
  error?: string | null;
}) {
  const label =
    status === "failed"
      ? "Error"
      : phase && phase !== "done"
        ? phase.replaceAll("_", " ")
        : status === "idle"
          ? "finished"
          : status;
  const badge = (
    <Badge
      variant="secondary"
      className={cn("capitalize", toneClass(status))}
      tabIndex={status === "failed" && error ? 0 : undefined}
    >
      {label}
    </Badge>
  );
  if (status === "failed" && error) {
    return <Tip content={error.slice(0, 300)}>{badge}</Tip>;
  }
  return badge;
}

export function OcrReviewBadge() {
  return (
    <Tip content="The transcription is waiting for your review — analysis continues once you accept or dismiss it.">
      <Badge className={TONE_BADGE.warning} tabIndex={0}>
        OCR review needed
      </Badge>
    </Tip>
  );
}

export function InboxBadge({ children = "inbox" }: { children?: React.ReactNode }) {
  return (
    <Badge variant="secondary" className="text-info">
      {children}
    </Badge>
  );
}
