/* THE hover-explanation affordance (AUDIT UI-U4): every tooltip in the
 * app goes through this wrapper — native `title=` renders the browser's
 * unstyled, delay-bound bubble and never fires for keyboard users.
 * Radix tooltips open on focus too, so the explanation reaches both.
 *
 * `content` may be null/empty for dynamic tooltips (e.g. error-only
 * hints): the child then renders bare, no wrapper cost.
 *
 * Disabled controls swallow pointer events, so tooltips on them never
 * fire — pass `mayDisable` where the child can be disabled and a
 * focusable span is interposed (the sanctioned shadcn pattern). */

import * as React from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export function Tip({
  content,
  side,
  mayDisable = false,
  children,
}: {
  content: React.ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  /** The child can be disabled: interpose a span so hover still works. */
  mayDisable?: boolean;
  children: React.ReactElement;
}) {
  if (content == null || content === false || content === "") return children;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {mayDisable ? (
          <span className="inline-flex" tabIndex={-1}>
            {children}
          </span>
        ) : (
          children
        )}
      </TooltipTrigger>
      <TooltipContent side={side}>{content}</TooltipContent>
    </Tooltip>
  );
}
