// THE base component for every block of a turn: work folds, proposals,
// and the model summary all render through this — one box, one header
// row, one type scale. Every panel collapses on a title click; only
// the default state differs (work folded, results open).

import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

/** Lead-in for a panel title, e.g. "Proposal", "Summary" — one clear
 * step BELOW the turn heading in the hierarchy. */
export function PanelTitle({ children }: { children: React.ReactNode }) {
  return <span className="font-medium text-foreground/80">{children}</span>;
}

/** Muted continuation of a panel title. */
export function PanelTitleMuted({ children }: { children: React.ReactNode }) {
  return <span className="text-muted-foreground/70">{children}</span>;
}

export function Panel({
  title,
  meta,
  defaultOpen = true,
  className,
  children,
}: {
  title: React.ReactNode;
  meta?: React.ReactNode;
  defaultOpen?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    // React only writes `open` when the prop CHANGES, so a constant
    // default never fights the user's toggle.
    <details open={defaultOpen} className={cn("group rounded-lg border bg-muted/20", className)}>
      <summary className="flex cursor-pointer items-center gap-2 px-4 py-2.5 select-none">
        <ChevronRight className="size-3 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" />
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-[13px]">{title}</div>
        <span className="flex-1" />
        {meta != null && <span className="shrink-0 pl-3">{meta}</span>}
      </summary>
      <div className="px-4 pb-4">{children}</div>
    </details>
  );
}
