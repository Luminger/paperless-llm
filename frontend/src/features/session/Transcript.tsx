// The model exchange, rendered in full: thinking blocks, tool calls
// (arguments AND return values), and prose — every part first-class
// and explorable, collapsed by default where it is noisy.
//
// ONE visual grid for every item: [20px icon column | content | meta].
// One type scale: text-sm sans for prose, text-xs for secondary rows,
// mono ONLY inside code/JSON. No asymmetric chat margins.

import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Brain,
  ChevronRight,
  MessageSquare,
  Sparkles,
  Wrench,
  XCircle,
} from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import type { TranscriptItem } from "../../api";
import { timingLabel } from "./timing";

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
  return s.length > 80 ? s.slice(0, 80) + "…" : s;
}

/** The shared row frame: icon column, content, right-aligned meta. */
function ItemRow({
  icon,
  meta,
  children,
  className,
}: {
  icon: React.ReactNode;
  meta?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("grid grid-cols-[1.25rem_1fr_auto] items-start gap-x-2 px-2 py-1.5", className)}>
      <span className="flex h-5 items-center justify-center">{icon}</span>
      <div className="min-w-0">{children}</div>
      {meta != null && (
        <span className="pt-0.5 pl-2 font-mono text-[10px] whitespace-nowrap text-muted-foreground/50">
          {meta}
        </span>
      )}
    </div>
  );
}

/** Collapsible variant of the row: the whole first line is the trigger. */
function CollapsibleRow({
  icon,
  meta,
  summary,
  children,
}: {
  icon: React.ReactNode;
  meta?: React.ReactNode;
  summary: React.ReactNode;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="group grid w-full grid-cols-[1.25rem_1fr_auto] items-start gap-x-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted/60">
        <span className="relative flex h-5 items-center justify-center">
          <span className="group-hover:opacity-0">{icon}</span>
          <ChevronRight
            className={cn(
              "absolute size-3.5 text-muted-foreground opacity-0 transition-transform group-hover:opacity-100",
              open && "rotate-90",
            )}
          />
        </span>
        <div className="min-w-0">{summary}</div>
        {meta != null && (
          <span className="pt-0.5 pl-2 font-mono text-[10px] whitespace-nowrap text-muted-foreground/50">
            {meta}
          </span>
        )}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 mb-1.5 ml-[1.6rem] border-l-2 border-border/60 pl-3">
          {children}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

/** One tool call: collapsed `name(arg summary)` row; expanding reveals
 * the full arguments and the complete return value. */
export function ToolCallItem({ item }: { item: TranscriptItem }) {
  const rejected = item.tool_rejected === true;
  return (
    <CollapsibleRow
      icon={
        rejected ? (
          <XCircle className="size-3.5 text-amber-600 dark:text-amber-400" />
        ) : (
          <Wrench className="size-3.5 text-muted-foreground/70" />
        )
      }
      meta={item.timing ? timingLabel(item.timing) : undefined}
      summary={
        <span className="block truncate font-mono text-xs leading-5">
          <span className="text-foreground/85">{item.tool_name}</span>
          <span className="text-muted-foreground/60">({argsSummary(item.tool_args)})</span>
          {rejected && (
            <span className="ml-2 font-sans text-amber-600 dark:text-amber-400">
              rejected
            </span>
          )}
        </span>
      }
    >
      <div className="space-y-2 py-1 text-xs">
        <div>
          <p className="mb-1 font-medium text-muted-foreground">Arguments</p>
          <pre className="overflow-x-auto rounded-md bg-muted/60 p-2 font-mono text-[11px] leading-4 whitespace-pre-wrap">
            {pretty(item.tool_args ?? {})}
          </pre>
        </div>
        <div>
          <p className="mb-1 font-medium text-muted-foreground">
            {rejected ? "Rejected with" : "Returned"}
          </p>
          <pre
            className={cn(
              "max-h-80 overflow-auto rounded-md bg-muted/60 p-2 font-mono text-[11px] leading-4 whitespace-pre-wrap",
              rejected &&
                "bg-amber-50 text-amber-900 dark:bg-amber-950/50 dark:text-amber-200",
            )}
          >
            {pretty(item.tool_result_full ?? item.tool_result ?? "(no result recorded)")}
          </pre>
        </div>
      </div>
    </CollapsibleRow>
  );
}

/** A thinking block: collapsed "Reasoning" row, expandable to the full
 * chain of thought. Shown, never hidden. */
export function ThinkingItem({ item }: { item: TranscriptItem }) {
  return (
    <CollapsibleRow
      icon={<Brain className="size-3.5 text-violet-500/80 dark:text-violet-400/80" />}
      summary={
        <span className="block truncate text-xs leading-5 text-muted-foreground">
          <span className="font-medium">Reasoning</span>
          <span className="text-muted-foreground/60"> — {item.content.slice(0, 100)}</span>
        </span>
      }
    >
      <div className="max-h-80 overflow-auto py-1 text-xs leading-5 whitespace-pre-wrap text-muted-foreground">
        {item.content}
      </div>
    </CollapsibleRow>
  );
}

const PROSE_CLASSES =
  "prose prose-sm dark:prose-invert max-w-none text-sm leading-6 prose-headings:mt-3 prose-headings:mb-1 prose-headings:text-sm prose-headings:font-semibold prose-p:my-1.5 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0 [&>*:first-child]:mt-0";

export function AgentProse({ item }: { item: TranscriptItem }) {
  return (
    <ItemRow
      icon={<Sparkles className="size-3.5 text-primary/80" />}
      meta={item.timing ? timingLabel(item.timing) : undefined}
    >
      <div className={PROSE_CLASSES}>
        <Markdown remarkPlugins={[remarkGfm]}>{item.content}</Markdown>
      </div>
    </ItemRow>
  );
}

/** A model prose body (markdown, one type scale). The model often
 * starts its summary with its own "Summary" heading — the panel header
 * already says that, so a redundant lead-in is dropped. */
export function ProseBody({ content }: { content: string }) {
  const lines = content.split("\n");
  const first = lines[0]?.trim() ?? "";
  if (/^(#{1,6}\s*)?[*_]{0,2}(updated\s+)?summary[*_]{0,2}\s*:?\s*$/i.test(first)) {
    content = lines.slice(1).join("\n").trimStart();
  }
  return (
    <div className={PROSE_CLASSES}>
      <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
    </div>
  );
}

export function UserMessage({ item }: { item: TranscriptItem }) {
  return (
    <ItemRow
      icon={<MessageSquare className="size-3.5 text-primary" />}
      className="rounded-md bg-primary/5"
    >
      <p className="text-sm leading-6 whitespace-pre-wrap">{item.content}</p>
    </ItemRow>
  );
}

export function Transcript({ items }: { items: TranscriptItem[] }) {
  return (
    <div>
      {items.map((item, i) => {
        switch (item.role) {
          case "user":
            if (item.origin === "pipeline") return null; // synthetic kickoff
            return <UserMessage key={i} item={item} />;
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
