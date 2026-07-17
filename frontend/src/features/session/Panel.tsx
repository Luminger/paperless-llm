// THE base component for every block of a turn: work folds, proposals,
// and the model summary all render through this — one box, one header
// row, one type scale.

import { cn } from "@/lib/utils";

function HeaderRow({
  title,
  meta,
}: {
  title: React.ReactNode;
  meta?: React.ReactNode;
}) {
  return (
    <>
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">{title}</div>
      <span className="flex-1" />
      {meta != null && <span className="shrink-0 pl-3">{meta}</span>}
    </>
  );
}

/** Bold lead-in for a panel title, e.g. "Proposal", "Summary". */
export function PanelTitle({ children }: { children: React.ReactNode }) {
  return <span className="font-medium">{children}</span>;
}

/** Muted continuation of a panel title. */
export function PanelTitleMuted({ children }: { children: React.ReactNode }) {
  return <span className="text-muted-foreground/70">{children}</span>;
}

export function Panel({
  title,
  meta,
  collapsible = false,
  className,
  children,
}: {
  title: React.ReactNode;
  meta?: React.ReactNode;
  collapsible?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  if (collapsible) {
    return (
      <details className={cn("rounded-lg border bg-muted/20", className)}>
        <summary className="flex cursor-pointer items-center px-4 py-3 select-none">
          <HeaderRow title={title} meta={meta} />
        </summary>
        <div className="px-4 pb-4">{children}</div>
      </details>
    );
  }
  return (
    <section className={cn("rounded-lg border bg-muted/20", className)}>
      <div className="flex items-center px-4 py-3">
        <HeaderRow title={title} meta={meta} />
      </div>
      <div className="px-4 pb-4">{children}</div>
    </section>
  );
}
