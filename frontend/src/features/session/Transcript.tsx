// The model exchange, rendered in full: thinking blocks, tool calls
// (arguments AND return values), and prose — every part first-class
// and explorable, collapsed by default where it is noisy.

import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Brain, ChevronRight, Wrench, XCircle } from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import type { TranscriptItem } from "../../api";
import { TimingChip, timingLabel } from "./timing";

function pretty(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function argsSummary(args: Record<string, unknown> | null | undefined): string {
  if (!args || Object.keys(args).length === 0) return "";
  const s = Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? JSON.stringify(v) : pretty(v)}`)
    .join(", ");
  return s.length > 90 ? s.slice(0, 90) + "…" : s;
}

/** One tool call: collapsed `name(arg summary)` row; expanding reveals
 * the full arguments and the complete return value. */
export function ToolCallItem({ item }: { item: TranscriptItem }) {
  const [open, setOpen] = useState(false);
  const rejected = item.tool_rejected === true;
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="group flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left font-mono text-xs text-muted-foreground transition-colors hover:bg-muted/60">
        <ChevronRight
          className={cn("size-3 shrink-0 transition-transform", open && "rotate-90")}
        />
        {rejected ? (
          <XCircle className="size-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
        ) : (
          <Wrench className="size-3.5 shrink-0" />
        )}
        <span className="text-foreground/80">{item.tool_name}</span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground/60">
          ({argsSummary(item.tool_args)})
        </span>
        {rejected && (
          <span className="shrink-0 text-amber-600 dark:text-amber-400">rejected</span>
        )}
        {item.timing && (
          <span className="shrink-0 text-muted-foreground/50">
            {timingLabel(item.timing)}
          </span>
        )}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 ml-5 space-y-2 border-l pl-3 text-xs">
          <div>
            <p className="mb-0.5 font-medium text-muted-foreground">Arguments</p>
            <pre className="overflow-x-auto rounded-md bg-muted/60 p-2 font-mono whitespace-pre-wrap">
              {pretty(item.tool_args ?? {})}
            </pre>
          </div>
          <div>
            <p className="mb-0.5 font-medium text-muted-foreground">
              {rejected ? "Rejected with" : "Returned"}
            </p>
            <pre
              className={cn(
                "max-h-80 overflow-auto rounded-md bg-muted/60 p-2 font-mono whitespace-pre-wrap",
                rejected && "bg-amber-50 text-amber-900 dark:bg-amber-950/50 dark:text-amber-200",
              )}
            >
              {pretty(item.tool_result_full ?? item.tool_result ?? "(no result recorded)")}
            </pre>
          </div>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

/** A thinking block: collapsed "Reasoning" row, expandable to the full
 * chain of thought. Shown, never hidden. */
export function ThinkingItem({ item }: { item: TranscriptItem }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="group flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/60">
        <ChevronRight
          className={cn("size-3 shrink-0 transition-transform", open && "rotate-90")}
        />
        <Brain className="size-3.5 shrink-0 text-violet-500 dark:text-violet-400" />
        <span className="italic">Reasoning</span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground/50 italic">
          {item.content.slice(0, 90)}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 ml-5 max-h-80 overflow-auto border-l pl-3 text-xs whitespace-pre-wrap text-muted-foreground italic">
          {item.content}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function AgentProse({ item }: { item: TranscriptItem }) {
  return (
    <div className="mr-8 rounded-lg border bg-card p-3 text-sm">
      <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1">
        <Markdown remarkPlugins={[remarkGfm]}>{item.content}</Markdown>
      </div>
      {item.timing && (
        <div className="mt-1 text-right">
          <TimingChip t={item.timing} />
        </div>
      )}
    </div>
  );
}

export function Transcript({ items }: { items: TranscriptItem[] }) {
  return (
    <div className="space-y-1.5">
      {items.map((item, i) => {
        switch (item.role) {
          case "user":
            if (item.origin === "pipeline") return null; // synthetic kickoff
            return (
              <div
                key={i}
                className="ml-8 rounded-lg border border-primary/25 bg-primary/5 p-3 text-sm whitespace-pre-wrap"
              >
                {item.content}
              </div>
            );
          case "thinking":
            return <ThinkingItem key={i} item={item} />;
          case "tool":
            return <ToolCallItem key={i} item={item} />;
          default:
            return <AgentProse key={i} item={item} />;
        }
      })}
    </div>
  );
}
