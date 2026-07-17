// The generic step frame: state dot, title, timestamps, kind-specific
// body, live trace, attempt history, error, retry/redo controls.

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { ErrorNotice } from "@/components/app/states";
import { api, type Proposal, type Step } from "../../api";
import { formatClock } from "../../lib/format";
import type { LiveActivity } from "../../hooks/useSessionEvents";
import { ProposalCard } from "../../components/ProposalCard";
import { DiffView } from "../../components/DiffView";
import { Transcript } from "./Transcript";
import { OcrGateBody } from "./OcrGate";
import { RedoDialog } from "./RedoDialog";

const STATE_DOT: Record<Step["state"], string> = {
  pending: "bg-muted-foreground/30",
  running: "bg-blue-500 animate-pulse",
  awaiting_user: "bg-amber-500",
  succeeded: "bg-primary",
  failed: "bg-destructive",
  superseded: "bg-muted-foreground/20",
  cancelled: "bg-muted-foreground/30",
};

function AttemptHistory({ step }: { step: Step }) {
  const finished = step.attempts.filter((a) => a.attempt != null);
  if (finished.length <= 1 && !step.attempts.some((a) => a.manual_retry_at)) return null;
  return (
    <ol className="space-y-0.5 text-xs text-muted-foreground">
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
    <div className="flex items-center gap-3 text-xs">
      {scheduled && (
        <span className="text-muted-foreground">
          Automatic retry {retriesDone + 1} of {maxRetries} at {formatClock(step.scheduled_at)}
        </span>
      )}
      {step.state === "failed" && maxRetries > 0 && (
        <span className="text-muted-foreground">
          all {maxRetries} automatic retr{maxRetries !== 1 ? "ies" : "y"} used
        </span>
      )}
      {/* Manual retries are never limited. */}
      {(step.state === "failed" || scheduled || step.state === "cancelled") && (
        <Button size="sm" onClick={() => retry.mutate()} disabled={retry.isPending}>
          Retry now
        </Button>
      )}
      {(step.state === "succeeded" || step.state === "failed") && (
        <Button
          size="sm"
          variant="secondary"
          onClick={() => setRedoOpen(true)}
          title="Do this step over with adjusted parameters"
        >
          Redo…
        </Button>
      )}
      {retry.error && (
        <span className="text-destructive">{String(retry.error).slice(0, 140)}</span>
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
    <div className="space-y-1 rounded-lg border border-blue-200 bg-blue-50/50 p-2 text-xs dark:border-blue-900 dark:bg-blue-950/30">
      {live.tools.map((t, i) => (
        <p key={i} className="font-mono text-muted-foreground">
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
  return (
    <div className="space-y-1 text-sm text-muted-foreground">
      {typeof step.input.instructions === "string" && (
        <p className="text-xs">with instructions: “{step.input.instructions}”</p>
      )}
      {pages != null && step.state !== "pending" && step.state !== "running" && (
        <p className="text-xs">
          {pages} page{pages !== 1 ? "s" : ""}
          {duration ? ` · ${duration}s` : ""}
          {resolution === "accepted" && " · new content accepted"}
          {resolution === "kept_existing" && " · existing content kept"}
        </p>
      )}
    </div>
  );
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
    <div className="space-y-4">
      {mine.map((p) =>
        p.status === "superseded" ? (
          <details key={p.id} className="rounded-lg border bg-muted/40 p-2">
            <summary className="cursor-pointer text-xs text-muted-foreground select-none">
              Proposal #{p.id} rev {p.revision} — superseded by a newer revision
            </summary>
            <div className="mt-2 opacity-70">
              <ProposalCard proposal={p} archived={archived} />
            </div>
          </details>
        ) : (
          <div key={p.id} className="rounded-lg border bg-muted/30 p-4">
            <ProposalCard proposal={p} archived={archived} />
          </div>
        ),
      )}
    </div>
  );
}

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
  return (
    <div className="space-y-3">
      <Transcript items={step.transcript} />
      <ProposalList ids={ids} proposals={proposals} archived={archived} />
      {step.state === "succeeded" && step.kind === "analysis" && ids.length === 0 && (
        <p className="text-sm text-muted-foreground">No changes proposed.</p>
      )}
    </div>
  );
}

function stepTitle(step: Step): string {
  const base = {
    ocr: "OCR",
    analysis: "Analysis",
    chat: "Conversation turn",
  }[step.kind];
  switch (step.state) {
    case "pending":
      return step.scheduled_at ? `${base} — retry scheduled` : `${base} — queued`;
    case "running":
      return `${base} — running…`;
    case "awaiting_user":
      return `${base} — your input needed`;
    case "failed":
      return `${base} — failed`;
    case "superseded":
      return `${base} — superseded`;
    case "cancelled":
      return `${base} — cancelled`;
    default:
      return base;
  }
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

/** Superseded steps stay fully inspectable: parameters, output, and
 * (for OCR) the diff the run produced at the time — collapsed by
 * default. */
function SupersededBody({ step, proposals }: { step: Step; proposals: Proposal[] }) {
  const text = step.result.text as string | undefined;
  const prev = step.result.previous_content as string | undefined;
  return (
    <details className="rounded-lg border bg-muted/30 p-2">
      <summary className="cursor-pointer text-xs text-muted-foreground/70 select-none">
        {paramsSummary(step)} — superseded, expand to inspect
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
  const collapsed = step.state === "superseded";
  return (
    <li className="relative pb-6 pl-8 last:pb-0">
      <span
        className={`absolute top-1 left-0 h-3.5 w-3.5 rounded-full ${STATE_DOT[step.state]}`}
      />
      <span className="absolute top-5 bottom-0 left-[6px] w-px bg-border" />
      <div className="mb-2 flex items-baseline gap-2">
        <p className={`font-medium ${collapsed ? "text-muted-foreground/60" : ""}`}>
          {stepTitle(step)}
        </p>
        <span className="text-xs text-muted-foreground/60">
          {formatClock(step.started_at ?? step.created_at)}
        </span>
      </div>
      {collapsed ? (
        <SupersededBody step={step} proposals={proposals} />
      ) : (
        <div className="space-y-2">
          {step.kind === "ocr" ? (
            <OcrBody step={step} />
          ) : (
            <TurnBody step={step} proposals={proposals} archived={archived} />
          )}
          {step.state === "running" && <LiveTrace live={live} />}
          <AttemptHistory step={step} />
          {step.error && step.state === "failed" && (
            <ErrorNotice error={step.error} />
          )}
          {!archived && <StepControls step={step} onChanged={onChanged} />}
        </div>
      )}
    </li>
  );
}
