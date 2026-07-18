// The one place a status string becomes a colored label.
// Dark-mode variants live here and nowhere else.

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const labels: Record<string, string> = {
  no_change: "no change needed",
  awaiting_user: "needs input",
};

const colors: Record<string, string> = {
  draft: "bg-muted text-muted-foreground",
  no_change: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  pending: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  applied: "bg-primary text-primary-foreground",
  superseded: "bg-muted text-muted-foreground line-through",
  // jobs & sessions & steps
  queued: "bg-muted text-muted-foreground",
  running: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  awaiting_user:
    "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  completed:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  succeeded:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  cancelled: "bg-muted text-muted-foreground",
  failed: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  idle: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge
      variant="secondary"
      className={cn("capitalize", colors[status] ?? "bg-muted")}
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
    phase && phase !== "done"
      ? phase.replaceAll("_", " ")
      : status === "idle"
        ? "finished"
        : status;
  return (
    <Badge
      variant="secondary"
      className={cn("capitalize", colors[status] ?? "bg-muted")}
    >
      {label}
    </Badge>
  );
}

export function OcrReviewBadge() {
  return (
    <Badge className="bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300">
      OCR review needed
    </Badge>
  );
}

export function InboxBadge({ children = "inbox" }: { children?: React.ReactNode }) {
  return (
    <Badge variant="secondary" className="text-blue-700 dark:text-blue-300">
      {children}
    </Badge>
  );
}
