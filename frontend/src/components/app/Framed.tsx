// THE box. The session trace defined the visual language — header
// strip (title left, meta right), body, footer strip (info left,
// actions right) — and every boxed surface reuses it: detail-page
// facts, content, sessions, history. One frame, everywhere.

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

// Shared with StepCard so trace turns and page boxes are literally the
// same frame.
export const frameHeaderClass =
  "flex h-10 items-center gap-2.5 bg-muted/30 px-4";
export const frameFooterClass =
  "flex min-h-10 items-center gap-2 border-t bg-muted/20 px-4 py-1.5";
export const frameTitleClass = "text-[15px] font-semibold tracking-tight";
export const frameMetaClass = "font-mono text-[11px] text-muted-foreground/60";

export function FramedCard({
  title,
  meta,
  footer,
  children,
  className,
  bodyClassName,
  collapsible = true,
  defaultOpen = true,
}: {
  title: React.ReactNode;
  /** Right side of the header (timestamps, counts). */
  meta?: React.ReactNode;
  /** Footer strip — put actions on the right, info on the left. */
  footer?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
  /** Header click folds the whole box (the trace-turn behavior).
   * On by default — every box is foldable. */
  collapsible?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const folded = collapsible && !open;
  return (
    <Card className={cn("gap-0 overflow-hidden py-0", className)}>
      <div
        className={cn(
          frameHeaderClass,
          !folded && "border-b",
          collapsible && "cursor-pointer select-none",
        )}
        onClick={collapsible ? () => setOpen(!open) : undefined}
      >
        {collapsible &&
          (open ? (
            <ChevronDown className="size-3.5 text-muted-foreground/60" />
          ) : (
            <ChevronRight className="size-3.5 text-muted-foreground/60" />
          ))}
        <span className={frameTitleClass}>{title}</span>
        <span className="flex-1" />
        {meta && <span className={frameMetaClass}>{meta}</span>}
      </div>
      {!folded && <div className={cn("p-4", bodyClassName)}>{children}</div>}
      {!folded && footer && <div className={frameFooterClass}>{footer}</div>}
    </Card>
  );
}
