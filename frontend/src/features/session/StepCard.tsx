// The generic step frame: ONE card per step with a uniform header
// strip (state, title, time, controls) and a consistently padded body.
// Kind-specific code only renders body content.

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ErrorNotice } from "@/components/app/states";
import { cn } from "@/lib/utils";
import { api, type Proposal, type Step, type TranscriptItem } from "../../api";
import { formatClock, formatDateTime } from "../../lib/format";
import type { LiveActivity } from "../../hooks/useSessionEvents";
import { ProposalCard, proposalKindLabel } from "../../components/ProposalCard";
import { DiffView } from "../../components/DiffView";
import { ProseBody, Transcript, UserMessage } from "./Transcript";
import { Panel, PanelTitle, PanelTitleMuted } from "./Panel";
import { StepTimingSummary, TimingChip } from "./timing";
import { StatusBadge } from "../../components/StatusBadge";
import { OcrGateBody } from "./OcrGate";
import { RedoDialog } from "./RedoDialog";

const STATE_DOT: Record<Step["state"], string> = {
  pending: "bg-muted-foreground/40",
  running: "bg-blue-500 animate-pulse",
  awaiting_user: "bg-amber-500",
  succeeded: "bg-primary",
  failed: "bg-destructive",
  superseded: "bg-muted-foreground/25",
  cancelled: "bg-muted-foreground/40",
};

const KIND_LABEL: Record<Step["kind"], string> = {
  ocr: "OCR",
  analysis: "Analysis",
  chat: "Conversation",
};

/** Turns count up: "Initial analysis", then "Turn 2", "Turn 3", … —
 * the ordinal comes from the session (live turns only); steps without
 * one (OCR, superseded history) keep their kind label. */
function stepLabel(step: Step, turn?: number): string {
  if (step.kind === "ocr" || turn == null) return KIND_LABEL[step.kind];
  if (turn === 1 && step.kind === "analysis") return "Initial analysis";
  return `Turn ${turn}`;
}

function stateSuffix(step: Step): string | null {
  switch (step.state) {
    case "pending":
      return step.scheduled_at ? "retry scheduled" : "queued";
    case "running":
      return "running…";
    case "awaiting_user":
      return "your input needed";
    case "failed":
      return "failed";
    case "superseded":
      return "superseded";
    case "cancelled":
      return "cancelled";
    default:
      return null;
  }
}

function AttemptHistory({ step }: { step: Step }) {
  const finished = step.attempts.filter((a) => a.attempt != null);
  if (finished.length <= 1 && !step.attempts.some((a) => a.manual_retry_at)) return null;
  return (
    <ol className="space-y-0.5 text-xs leading-5 text-muted-foreground">
      {step.attempts.map((a, i) =>
        a.manual_retry_at ? (
          <li key={i} className="text-muted-foreground/60">
            manual retry requested · {formatClock(a.manual_retry_at)}
          </li>
        ) : (
          <li key={i}>
            <span className="font-medium">Attempt {a.attempt}</span>
            {a.started_at && ` · ${formatClock(a.started_at)}`}
            {a.finished_at ? ` → ${formatClock(a.finished_at)}` : " → interrupted"}
            {a.error ? (
              <span className="text-destructive"> · {a.error.slice(0, 140)}</span>
            ) : (
              <span className="text-primary"> · ok</span>
            )}
          </li>
        ),
      )}
    </ol>
  );
}

function StepControls({ step, onChanged }: { step: Step; onChanged: () => void }) {
  const [redoOpen, setRedoOpen] = useState(false);
  const retry = useMutation({
    mutationFn: () => api.retryStep(step.session_id, step.id),
    onSuccess: onChanged,
  });
  const redo = useMutation({
    mutationFn: (input: Record<string, unknown>) =>
      api.redoStep(step.session_id, step.id, input),
    onSuccess: () => {
      setRedoOpen(false);
      onChanged();
    },
  });
  const scheduled = step.state === "pending" && step.scheduled_at;
  const maxRetries = Math.max(0, step.max_attempts - 1);
  const retriesDone = Math.max(0, step.attempt_count - 1);
  return (
    <div className="flex items-center gap-2">
      {scheduled && (
        <span className="text-xs text-muted-foreground">
          auto-retry {retriesDone + 1}/{maxRetries} at {formatClock(step.scheduled_at)}
        </span>
      )}
      {step.state === "failed" && maxRetries > 0 && (
        <span className="text-xs text-muted-foreground">
          {maxRetries} auto-retr{maxRetries !== 1 ? "ies" : "y"} used
        </span>
      )}
      {/* Manual retries are never limited. */}
      {(step.state === "failed" || scheduled || step.state === "cancelled") && (
        <Button
          size="sm"
          className="h-7 px-2.5 text-xs"
          onClick={() => retry.mutate()}
          disabled={retry.isPending}
        >
          Retry now
        </Button>
      )}
      {(step.state === "succeeded" || step.state === "failed") && (
        <Button
          size="sm"
          variant="outline"
          className="h-7 px-2.5 text-xs"
          onClick={() => setRedoOpen(true)}
          title="Do this step over with adjusted parameters"
        >
          Redo…
        </Button>
      )}
      {retry.error && (
        <span className="text-xs text-destructive">{String(retry.error).slice(0, 140)}</span>
      )}
      {redoOpen && (
        <RedoDialog
          step={step}
          open={redoOpen}
          onOpenChange={setRedoOpen}
          busy={redo.isPending}
          error={redo.error}
          onConfirm={(input) => redo.mutate(input)}
        />
      )}
    </div>
  );
}

function OcrBody({ step }: { step: Step }) {
  if (step.state === "awaiting_user") return <OcrGateBody step={step} />;
  const resolution = step.result.resolution as string | undefined;
  const pages = step.result.pages as number | undefined;
  const duration = step.result.duration_s as number | undefined;
  const bits: string[] = [];
  if (typeof step.input.instructions === "string")
    bits.push(`instructions: “${step.input.instructions}”`);
  if (pages != null && step.state !== "pending" && step.state !== "running") {
    bits.push(`${pages} page${pages !== 1 ? "s" : ""}${duration ? ` · ${duration}s` : ""}`);
    if (resolution === "accepted") bits.push("new content accepted");
    if (resolution === "kept_existing") bits.push("existing content kept");
  }
  if (bits.length === 0) return null;
  return <p className="text-xs leading-5 text-muted-foreground">{bits.join(" · ")}</p>;
}

/** The user's decision is part of the record: who applied it. */
export function DecidedBy({ p }: { p: Proposal }) {
  if (!p.applied || !p.applied_by) return null;
  const label =
    p.applied_by === "system"
      ? "applied automatically"
      : p.user_payload != null
        ? "accepted by you (edited)"
        : "accepted by you";
  return <PanelTitleMuted>{label}</PanelTitleMuted>;
}

function stepProposals(step: Step, proposals: Proposal[]): Proposal[] {
  const ids = (step.result.proposal_ids as number[] | undefined) ?? [];
  const byId = new Map(proposals.map((p) => [p.id, p]));
  return ids
    .map((id) => byId.get(id))
    .filter((p): p is Proposal => p != null && p.kind !== "replace_content");
}

function WorkFold({ items, open = false }: { items: TranscriptItem[]; open?: boolean }) {
  const toolCount = items.filter((it) => it.role === "tool").length;
  const thinkingCount = items.filter((it) => it.role === "thinking").length;
  const label =
    [
      toolCount > 0 && `${toolCount} tool call${toolCount !== 1 ? "s" : ""}`,
      thinkingCount > 0 &&
        `${thinkingCount} reasoning step${thinkingCount !== 1 ? "s" : ""}`,
    ]
      .filter(Boolean)
      .join(" · ") || `${items.length} steps`;
  return (
    // `open` flips false when the live tail gets sealed by a proposal —
    // the panel closes itself and the timeline moves on.
    <Panel
      defaultOpen={open}
      title={
        <>
          <PanelTitle>{open ? "The agent's work…" : "The agent's work"}</PanelTitle>
          <PanelTitleMuted>{label}</PanelTitleMuted>
        </>
      }
    >
      <Transcript items={items} />
    </Panel>
  );
}

/** ONE turn renderer for both the finished transcript and the live
 * stream — the UI builds piece by piece out of the REAL components:
 * work (reasoning, exploratory tool calls) folds into panels that
 * auto-close as soon as the next proposal pops in, proposals render in
 * place of their propose_* calls, the summary stays fixed. While
 * streaming, the trailing work panel is open and growing. */
function TurnBody({
  step,
  proposals,
  archived,
  live,
}: {
  step: Step;
  proposals: Proposal[];
  archived: boolean;
  live?: LiveActivity;
}) {
  const streaming = step.state === "running" || step.state === "pending";
  // Live proposals are matched via step_id (result.proposal_ids only
  // exists once the turn finished).
  const mine = streaming
    ? proposals.filter(
        (p) => p.step_id === step.id && p.kind !== "replace_content",
      )
    : stepProposals(step, proposals);
  const items = streaming ? (live?.items ?? []) : step.transcript;

  // The summary is the LAST agent prose — but only once the turn is
  // done; mid-stream prose is just the growing tail of the work.
  let summaryIdx = -1;
  if (!streaming) {
    for (let i = items.length - 1; i >= 0; i--) {
      if (items[i].role === "agent") {
        summaryIdx = i;
        break;
      }
    }
  }

  // Walk chronologically; successful propose_* calls become their
  // proposal (matched by the id in the tool result, order fallback).
  const out: React.ReactNode[] = [];
  let fold: typeof items = [];
  let cursor = 0;
  const consumed = new Set<number>();
  const flush = (liveTail = false) => {
    if (fold.length > 0) {
      out.push(
        <WorkFold key={`fold-${out.length}`} items={fold} open={liveTail} />,
      );
      fold = [];
    }
  };

  // The user's own instructions for this turn lead it — their box,
  // their styling, collapsible like everything else.
  const instructions = step.input.instructions;
  if (typeof instructions === "string" && instructions) {
    out.push(
      <Panel
        key="instructions"
        className="border-primary/25 bg-primary/5"
        title={
          <>
            <PanelTitle>Your instructions</PanelTitle>
          </>
        }
      >
        <p className="text-sm leading-6 whitespace-pre-wrap">{instructions}</p>
      </Panel>,
    );
  }
  // While streaming, the transcript slice doesn't exist yet — the
  // user's message comes straight from the step input so their box is
  // there from the first moment, not after the run finishes.
  if (
    streaming &&
    step.kind === "chat" &&
    typeof step.input.content === "string" &&
    step.input.auto !== true
  ) {
    out.push(
      <UserMessage
        key="pending-user"
        item={
          {
            role: "user",
            content: step.input.content,
            origin: "chat",
          } as TranscriptItem
        }
      />,
    );
  }
  const renderProposal = (p: Proposal) =>
    p.status === "superseded" ? (
      <details key={`p-${p.id}`} className="rounded-md border border-dashed px-3 py-2">
        <summary className="cursor-pointer text-xs text-muted-foreground select-none">
          Revision {p.revision} of this proposal — superseded by a newer one
        </summary>
        <div className="mt-3 opacity-70">
          <ProposalCard proposal={p} archived={archived} />
        </div>
      </details>
    ) : (
      <Panel
        key={`p-${p.id}`}
        // Decided proposals are history: folded on load (and they fold
        // themselves the moment the decision lands).
        defaultOpen={p.status !== "applied" && p.status !== "no_change"}
        title={
          <>
            <PanelTitle>Proposal</PanelTitle>
            <PanelTitleMuted>{proposalKindLabel(p)}</PanelTitleMuted>
            <StatusBadge status={p.status} />
            {p.applied && <DecidedBy p={p} />}
            {p.revision > 1 && <PanelTitleMuted>revision {p.revision}</PanelTitleMuted>}
          </>
        }
      >
        <ProposalCard proposal={p} archived={archived} withHeader={false} />
      </Panel>
    );

  items.forEach((item, idx) => {
    if (item.role === "user") {
      if (item.origin === "pipeline") return;
      flush();
      out.push(<UserMessage key={`u-${idx}`} item={item} />);
      return;
    }
    // Streaming: prose that may still become the summary stays in the
    // open tail; never fold the item currently being generated.

    if (idx === summaryIdx) {
      flush();
      out.push(
        <Panel
          key="summary"
          title={<PanelTitle>Summary</PanelTitle>}
          meta={<TimingChip t={item.timing} />}
        >
          <ProseBody content={item.content} />
        </Panel>,
      );
      return;
    }
    if (
      item.role === "tool" &&
      item.tool_name?.startsWith("propose_") &&
      !item.tool_rejected
    ) {
      // Structural link from the backend transcript; the positional
      // cursor only covers legacy histories persisted before it existed.
      const p =
        (item.proposal_id != null &&
          mine.find((x) => x.id === item.proposal_id)) ||
        mine[cursor] ||
        null;
      if (p && !consumed.has(p.id)) {
        consumed.add(p.id);
        cursor = mine.indexOf(p) + 1;
        flush();
        out.push(renderProposal(p));
        return;
      }
    }
    fold.push(item);
  });
  // While streaming the trailing fold stays OPEN (it is the live
  // view); when the next proposal arrives, flush() runs with
  // liveTail=false and the panel closes automatically.
  flush(streaming);
  if (!streaming) {
    // Safety net: proposals not matched to a tool call still render.
    for (const p of mine) {
      if (!consumed.has(p.id)) out.push(renderProposal(p));
    }
  }

  return (
    <div className="space-y-3">
      {out}
      {streaming && (
        <p className="px-2 text-xs text-muted-foreground">
          <span className="mr-1.5 inline-block size-2 animate-pulse rounded-full bg-blue-500" />
          {live && live.tokens > 0
            ? `generating… ${live.tokens} tokens`
            : "working…"}
        </p>
      )}
      {step.state === "succeeded" && step.kind === "analysis" && mine.length === 0 && (
        <p className="px-2 text-sm text-muted-foreground">No changes proposed.</p>
      )}
    </div>
  );
}

function paramsSummary(step: Step): string {
  const parts: string[] = [];
  if (typeof step.input.instructions === "string")
    parts.push(`instructions: “${step.input.instructions}”`);
  if (step.input.dpi != null) parts.push(`dpi: ${step.input.dpi}`);
  if (typeof step.input.content === "string")
    parts.push(`message: “${String(step.input.content).slice(0, 60)}”`);
  if (step.input.gate != null) parts.push(`gate: ${step.input.gate}`);
  return parts.length ? parts.join(" · ") : "default parameters";
}

/** Superseded / outdated steps stay fully inspectable — collapsed by
 * default. */
function SupersededBody({
  step,
  proposals,
  label,
}: {
  step: Step;
  proposals: Proposal[];
  label?: string;
}) {
  const text = step.result.text as string | undefined;
  const prev = step.result.previous_content as string | undefined;
  return (
    <details>
      <summary className="cursor-pointer text-xs text-muted-foreground/70 select-none">
        {label ?? paramsSummary(step)} — expand to inspect
      </summary>
      <div className="mt-3 space-y-3 opacity-80">
        {step.kind === "ocr" ? (
          text != null ? (
            <DiffView oldText={prev ?? ""} newText={text} />
          ) : (
            <p className="text-xs text-muted-foreground/70">
              no OCR output recorded for this run
            </p>
          )
        ) : (
          <TurnBody step={step} proposals={proposals} archived={true} />
        )}
        <AttemptHistory step={step} />
      </div>
    </details>
  );
}

export function StepCard({
  step,
  proposals,
  live,
  onChanged,
  archived,
  turn,
}: {
  step: Step;
  proposals: Proposal[];
  live: LiveActivity | undefined;
  onChanged: () => void;
  archived: boolean;
  /** 1-based ordinal among the session's live turns (non-OCR steps). */
  turn?: number;
}) {
  const superseded = step.state === "superseded";
  // A finished turn whose proposals were ALL revised away is history —
  // the whole block folds, exactly like a redo-superseded step.
  const mine = stepProposals(step, proposals);
  const outdated =
    !superseded &&
    step.state === "succeeded" &&
    step.kind !== "ocr" &&
    mine.length > 0 &&
    mine.every((p) => p.status === "superseded");
  const collapsed = superseded || outdated;
  const suffix = outdated ? "superseded by a later revision" : stateSuffix(step);
  // Whole-turn fold: its own state, never touching the folds inside.
  const [folded, setFolded] = useState(false);
  return (
    <Card className={cn("gap-0 overflow-hidden py-0", collapsed && "border-dashed")}>
      {/* Uniform header strip — click anywhere to fold the whole turn. */}
      <div
        role="button"
        aria-expanded={!folded}
        className={cn(
          "flex h-10 cursor-pointer items-center gap-2.5 bg-muted/30 px-4 select-none",
          !folded && "border-b",
        )}
        onClick={() => setFolded(!folded)}
      >
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform",
            !folded && "rotate-90",
          )}
        />
        <span
          className={cn(
            "size-2 shrink-0 rounded-full",
            outdated ? STATE_DOT.superseded : STATE_DOT[step.state],
          )}
        />
        <span className={cn("text-sm font-medium", collapsed && "text-muted-foreground/70")}>
          {stepLabel(step, collapsed ? undefined : turn)}
        </span>
        {suffix && (
          <Badge variant="secondary" className="text-xs font-normal text-muted-foreground">
            {suffix}
          </Badge>
        )}
        <span className="flex-1" />
        <span className="font-mono text-[11px] text-muted-foreground/60">
          {formatDateTime(step.started_at ?? step.created_at)}
        </span>
      </div>

      {folded ? null : (
      <div className="space-y-3 px-4 py-3">
        {collapsed ? (
          <SupersededBody
            step={step}
            proposals={proposals}
            label={outdated ? "its proposals were revised in a later turn" : undefined}
          />
        ) : (
          <>
            {step.kind === "ocr" ? (
              <OcrBody step={step} />
            ) : (
              <TurnBody
                step={step}
                proposals={proposals}
                archived={archived}
                live={live}
              />
            )}
            <AttemptHistory step={step} />
            {step.error && step.state === "failed" && <ErrorNotice error={step.error} />}
          </>
        )}
      </div>
      )}

      {/* Footer: whole-turn cost on the left, step actions on the
          right — actions live with the turn they act on. */}
      {!collapsed && !folded && (
        <StepFooter
          step={step}
          archived={archived}
          onChanged={onChanged}
        />
      )}
    </Card>
  );
}

function StepFooter({
  step,
  archived,
  onChanged,
}: {
  step: Step;
  archived: boolean;
  onChanged: () => void;
}) {
  const timing =
    step.kind !== "ocr" && step.transcript.length > 0 ? (
      <StepTimingSummary items={step.transcript} />
    ) : step.kind === "ocr" && step.result.duration_s != null ? (
      <span className="text-xs text-muted-foreground/70">
        {String(step.result.pages ?? "?")} page
        {step.result.pages !== 1 ? "s" : ""} · {String(step.result.duration_s)}s
      </span>
    ) : null;
  const showControls =
    !archived &&
    (step.state === "succeeded" ||
      step.state === "failed" ||
      step.state === "cancelled" ||
      (step.state === "pending" && step.scheduled_at != null));
  if (!timing && !showControls) return null;
  return (
    <div className="flex min-h-10 items-center gap-2 border-t bg-muted/20 px-4 py-1.5">
      {timing}
      <span className="flex-1" />
      {showControls && <StepControls step={step} onChanged={onChanged} />}
    </div>
  );
}
