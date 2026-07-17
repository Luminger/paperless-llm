// The generic step frame: ONE card per step with a uniform header
// strip (state, title, time, controls) and a consistently padded body.
// Kind-specific code only renders body content.

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { ErrorNotice } from "@/components/app/states";
import { cn } from "@/lib/utils";
import { api, type Proposal, type Step } from "../../api";
import { formatClock } from "../../lib/format";
import type { LiveActivity } from "../../hooks/useSessionEvents";
import { ProposalCard } from "../../components/ProposalCard";
import { DiffView } from "../../components/DiffView";
import { AgentProse, Transcript, UserMessage } from "./Transcript";
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
          className="h-6 px-2 text-xs"
          onClick={() => retry.mutate()}
          disabled={retry.isPending}
        >
          Retry now
        </Button>
      )}
      {(step.state === "succeeded" || step.state === "failed") && (
        <Button
          size="sm"
          variant="ghost"
          className="h-6 px-2 text-xs text-muted-foreground"
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

function LiveTrace({ live }: { live: LiveActivity | undefined }) {
  if (!live || (live.tools.length === 0 && live.tokens === 0)) return null;
  return (
    <div className="space-y-1 rounded-md border border-blue-200 bg-blue-50/50 p-2 text-xs leading-5 dark:border-blue-900 dark:bg-blue-950/30">
      {live.tools.map((t, i) => (
        <p key={i} className="truncate font-mono text-muted-foreground">
          → {t.tool}
          {t.args && t.args !== "{}" && (
            <span className="text-muted-foreground/60"> {t.args}</span>
          )}
        </p>
      ))}
      {live.tokens > 0 && (
        <p className="text-muted-foreground">
          <span className="mr-1 inline-block size-2 animate-pulse rounded-full bg-blue-500" />
          streaming… {live.tokens} tokens
        </p>
      )}
      {live.textTail && (
        <p className="font-mono text-[11px] whitespace-pre-wrap text-muted-foreground/70">
          …{live.textTail.slice(-200)}
        </p>
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

function ProposalList({
  ids,
  proposals,
  archived,
}: {
  ids: number[];
  proposals: Proposal[];
  archived: boolean;
}) {
  const byId = new Map(proposals.map((p) => [p.id, p]));
  const mine = ids
    .map((id) => byId.get(id))
    .filter((p): p is Proposal => p != null && p.kind !== "replace_content");
  if (mine.length === 0) return null;
  return (
    <div className="space-y-3">
      {mine.map((p) =>
        p.status === "superseded" ? (
          <details key={p.id} className="rounded-md border border-dashed px-3 py-2">
            <summary className="cursor-pointer text-xs text-muted-foreground select-none">
              Proposal #{p.id} rev {p.revision} — superseded by a newer revision
            </summary>
            <div className="mt-3 opacity-70">
              <ProposalCard proposal={p} archived={archived} />
            </div>
          </details>
        ) : (
          <div key={p.id}>
            <Separator className="mb-3" />
            <ProposalCard proposal={p} archived={archived} />
          </div>
        ),
      )}
    </div>
  );
}

/** A finished turn folds its WORK (reasoning + tool calls +
 * intermediate prose) into one collapsed section — the same pattern as
 * superseded proposals. The final summary stays fixed and visible,
 * just like the proposals. */
function TurnBody({
  step,
  proposals,
  archived,
}: {
  step: Step;
  proposals: Proposal[];
  archived: boolean;
}) {
  const ids = (step.result.proposal_ids as number[] | undefined) ?? [];
  const items = step.transcript;

  // The summary is the LAST agent prose of the turn.
  let summaryIdx = -1;
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].role === "agent") {
      summaryIdx = i;
      break;
    }
  }
  const userMessages = items.filter(
    (it) => it.role === "user" && it.origin !== "pipeline",
  );
  const trace = items.filter(
    (it, idx) => idx !== summaryIdx && it.role !== "user",
  );
  const toolCount = trace.filter((it) => it.role === "tool").length;
  const thinkingCount = trace.filter((it) => it.role === "thinking").length;
  const traceLabel = [
    toolCount > 0 && `${toolCount} tool call${toolCount !== 1 ? "s" : ""}`,
    thinkingCount > 0 && `${thinkingCount} reasoning step${thinkingCount !== 1 ? "s" : ""}`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="space-y-3">
      {userMessages.map((it, i) => (
        <UserMessage key={i} item={it} />
      ))}
      {trace.length > 0 && (
        <details className="rounded-md border border-dashed px-3 py-2">
          <summary className="cursor-pointer text-xs text-muted-foreground select-none">
            The agent's work — {traceLabel || `${trace.length} steps`}, expand to inspect
          </summary>
          <div className="mt-2">
            <Transcript items={trace} />
          </div>
        </details>
      )}
      {summaryIdx >= 0 && <AgentProse item={items[summaryIdx]} />}
      <ProposalList ids={ids} proposals={proposals} archived={archived} />
      {step.state === "succeeded" && step.kind === "analysis" && ids.length === 0 && (
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

/** Superseded steps stay fully inspectable — collapsed by default. */
function SupersededBody({ step, proposals }: { step: Step; proposals: Proposal[] }) {
  const text = step.result.text as string | undefined;
  const prev = step.result.previous_content as string | undefined;
  return (
    <details>
      <summary className="cursor-pointer text-xs text-muted-foreground/70 select-none">
        {paramsSummary(step)} — expand to inspect
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
}: {
  step: Step;
  proposals: Proposal[];
  live: LiveActivity | undefined;
  onChanged: () => void;
  archived: boolean;
}) {
  const superseded = step.state === "superseded";
  const suffix = stateSuffix(step);
  return (
    <Card className={cn("gap-0 overflow-hidden py-0", superseded && "border-dashed")}>
      {/* Uniform header strip. */}
      <div className="flex h-10 items-center gap-2.5 border-b bg-muted/30 px-4">
        <span className={cn("size-2 shrink-0 rounded-full", STATE_DOT[step.state])} />
        <span className={cn("text-sm font-medium", superseded && "text-muted-foreground/70")}>
          {KIND_LABEL[step.kind]}
        </span>
        {suffix && (
          <Badge variant="secondary" className="text-xs font-normal text-muted-foreground">
            {suffix}
          </Badge>
        )}
        <span className="flex-1" />
        {!archived && !superseded && <StepControls step={step} onChanged={onChanged} />}
        <span className="font-mono text-[11px] text-muted-foreground/60">
          {formatClock(step.started_at ?? step.created_at)}
        </span>
      </div>

      <div className="space-y-3 px-4 py-3">
        {superseded ? (
          <SupersededBody step={step} proposals={proposals} />
        ) : (
          <>
            {step.kind === "ocr" ? (
              <OcrBody step={step} />
            ) : (
              <TurnBody step={step} proposals={proposals} archived={archived} />
            )}
            {step.state === "running" && <LiveTrace live={live} />}
            <AttemptHistory step={step} />
            {step.error && step.state === "failed" && <ErrorNotice error={step.error} />}
          </>
        )}
      </div>
    </Card>
  );
}
