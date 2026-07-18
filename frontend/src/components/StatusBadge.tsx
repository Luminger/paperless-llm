// The one place a status string becomes a colored label. Colors come
// from the semantic tone tokens (lib/labels.ts) — no raw palette here.

import { Badge } from "@/components/ui/badge";
import { STATUS_TONE, TONE_BADGE } from "@/lib/labels";
import { cn } from "@/lib/utils";

const labels: Record<string, string> = {
  no_change: "no change needed",
  awaiting_user: "needs input",
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
  return (
    <Badge
      variant="secondary"
      className={cn("capitalize", toneClass(status))}
    >
      {labels[status] ?? status.replaceAll("_", " ")}
    </Badge>
  );
}

/** Session badge: color follows the STATUS, text shows the PHASE. */
export function SessionStatusBadge({
  status,
  phase,
}: {
  status: string;
  phase?: string | null;
}) {
  const label =
    status === "failed"
      ? "Error"
      : phase && phase !== "done"
        ? phase.replaceAll("_", " ")
        : status === "idle"
          ? "finished"
          : status;
  return (
    <Badge
      variant="secondary"
      className={cn("capitalize", toneClass(status))}
    >
      {label}
    </Badge>
  );
}

export function OcrReviewBadge() {
  return (
    <Badge className={TONE_BADGE.warning}>
      OCR review needed
    </Badge>
  );
}

export function InboxBadge({ children = "inbox" }: { children?: React.ReactNode }) {
  return (
    <Badge variant="secondary" className="text-info">
      {children}
    </Badge>
  );
}
