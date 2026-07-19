// The generic step frame: ONE card per step with a uniform header
// strip (state, title, time, controls) and a consistently padded body.
// Kind-specific code only renders body content.

import { useState } from "react";
import { Tip } from "@/components/app/Tip";
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
import { ProposalCard } from "../../components/ProposalCard";
import { isInternalKind, proposalKindLabel } from "../../lib/proposal-kinds";
import { frameFooterClass, frameHeaderClass } from "@/components/app/Framed";
import { DiffView } from "../../components/DiffView";
import { ProseBody, Transcript, UserMessage, isRenderable } from "./Transcript";
import { deriveTurnView, stepProposals } from "./turn-view";
import { Panel, PanelTitle, PanelTitleMuted } from "./Panel";
import { StepTimingSummary } from "./timing";
import { StatusBadge } from "../../components/StatusBadge";
import { OcrGateBody } from "./OcrGate";
import { RedoDialog } from "./RedoDialog";

const STATE_DOT: Record<Step["state"], string> = {
  pending: "bg-muted-foreground/40",
  running: "bg-info animate-pulse",
  awaiting_user: "bg-warning",
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
        <Tip content="Do this step over with adjusted parameters">
          <Button
            size="sm"
            variant="outline"
            className="h-7 px-2.5 text-xs"
            onClick={() => setRedoOpen(true)}
          >
            Redo…
          </Button>
        </Tip>
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

type OcrBatch = {
  pages?: string;
  rotated?: number[];
  duration_s?: number;
  output_tokens?: number;
  tps?: number;
  text?: string;
};

function batchStats(b: OcrBatch): string {
  const bits: string[] = [];
  if (b.duration_s != null) bits.push(`${b.duration_s}s`);
  if (b.output_tokens != null) bits.push(`${b.output_tokens.toLocaleString()} tok`);
  if (b.tps != null) bits.push(`${b.tps} tok/s`);
  if (b.rotated?.length)
    bits.push(`auto-rotated p. ${b.rotated.join(", ")}`);
  return bits.join(" · ");
}

/** Live OCR progress: which pages are batched, what came back, and the
 * same call metrics agent turns show — updated after every batch. */
function OcrProgressView({ progress }: { progress: Record<string, unknown> }) {
  const total = Number(progress.total_pages ?? 0);
  const done = Number(progress.done_pages ?? 0);
  const batches = (progress.batches as OcrBatch[] | undefined) ?? [];
  const latest = batches[batches.length - 1];
  return (
    <div className="space-y-2" aria-label="ocr progress">
      <div className="flex items-center gap-3 text-sm">
        <span className="font-medium">
          Transcribing — page {done} of {total}
        </span>
        <span className="text-xs text-muted-foreground">
          batch {batches.length} of {String(progress.total_batches ?? "?")}
        </span>
      </div>
      {total > 0 && (
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${Math.min(100, (done / total) * 100)}%` }}
          />
        </div>
      )}
      <ul className="space-y-1 text-xs text-muted-foreground">
        {batches.map((b, i) => (
          <li key={`${b.pages}-${i}`} className="flex gap-2">
            <span className="font-medium text-foreground/80">
              pages {b.pages}
            </span>
            <span>{batchStats(b)}</span>
          </li>
        ))}
      </ul>
      {latest?.text != null && (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground/70 select-none">
            latest returned text (pages {latest.pages})
          </summary>
          <pre className="mt-2 max-h-64 overflow-y-auto rounded-md bg-muted/40 p-3 font-mono text-xs leading-5 break-words whitespace-pre-wrap">
            {latest.text}
          </pre>
        </details>
      )}
    </div>
  );
}

/** Finished runs keep the per-batch metrics — the fold shows how the
 * document was chunked and how each call performed. */
function OcrBatches({ batches }: { batches: OcrBatch[] }) {
  return (
    <details>
      <summary className="cursor-pointer text-xs text-muted-foreground/70 select-none">
        {batches.length} OCR call{batches.length === 1 ? "" : "s"} — expand for
        per-batch metrics
      </summary>
      <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
        {batches.map((b, i) => (
          <li key={`${b.pages}-${i}`} className="flex gap-2">
            <span className="font-medium text-foreground/80">
              pages {b.pages}
            </span>
            <span>{batchStats(b)}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

/** The OCR footer line — same statistics vocabulary as agent turns:
 * pages, DPI, wall time, tokens, generation speed, rotations. */
export function OcrTimingSummary({ result }: { result: Record<string, unknown> }) {
  const batches = (result.batches as OcrBatch[] | undefined) ?? [];
  const tokens = batches.reduce((n, b) => n + (b.output_tokens ?? 0), 0);
  const withTps = batches.filter((b) => b.tps != null);
  const avgTps = withTps.length
    ? withTps.reduce((n, b) => n + (b.tps ?? 0), 0) / withTps.length
    : null;
  const rotated = batches.reduce((n, b) => n + (b.rotated?.length ?? 0), 0);
  return (
    <span className="text-xs text-muted-foreground/70">
      {String(result.pages ?? "?")} page
      {result.pages !== 1 ? "s" : ""}
      {result.dpi != null && ` · ${String(result.dpi)} DPI`}
      {" · "}
      {String(result.duration_s)}s
      {tokens > 0 && ` · ${tokens.toLocaleString()} tok`}
      {avgTps != null && ` · ${avgTps.toFixed(1)} tok/s`}
      {rotated > 0 && ` · ${rotated} page${rotated === 1 ? "" : "s"} auto-rotated`}
    </span>
  );
}

function OcrBody({ step, proposals }: { step: Step; proposals: Proposal[] }) {
  const resolution = step.result.resolution as string | undefined;
  const text = step.result.text as string | undefined;
  const prev = step.result.previous_content as string | undefined;
  // The accepted OCR text is written via an internal journaled
  // proposal — the SAME record proposals use, so the outcome row can
  // use the same badge + decided-by treatment as every proposal.
  const contentProposal = proposals.find(
    (p) => p.step_id === step.id && isInternalKind(p.kind),
  );
  const instructions =
    typeof step.input.instructions === "string" ? step.input.instructions : null;
  return (
    <div className="space-y-3">
      {/* The user's guidance renders exactly like on agent turns. */}
      {instructions && (
        <Panel
          className="border-primary/25 bg-primary/5"
          title={<PanelTitle>Your instructions</PanelTitle>}
        >
          <p className="text-sm leading-6 whitespace-pre-wrap">{instructions}</p>
        </Panel>
      )}
      {step.state === "running" && step.result.progress != null ? (
        <OcrProgressView progress={step.result.progress as Record<string, unknown>} />
      ) : step.state === "awaiting_user" ? (
        <OcrGateBody key={step.id} step={step} />
      ) : (
        resolution && (
          <>
            <p className="flex items-center gap-2 text-sm">
              {contentProposal ? (
                <>
                  <StatusBadge status={contentProposal.status} />
                  {contentProposal.applied && <DecidedBy p={contentProposal} />}
                </>
              ) : (
                <>
                  <StatusBadge status="no_change" />
                  <PanelTitleMuted>
                    {resolution === "kept_existing"
                      ? "existing content kept"
                      : "content unchanged"}
                  </PanelTitleMuted>
                </>
              )}
            </p>
            {/* The decision is history, but WHAT changed stays
                inspectable — the same diff, read-only. */}
            {text != null && <DiffView oldText={prev ?? ""} newText={text} />}
          </>
        )
      )}
      {step.state !== "running" &&
        Array.isArray(step.result.batches) &&
        step.result.batches.length > 0 && (
          <OcrBatches batches={step.result.batches as OcrBatch[]} />
        )}
    </div>
  );
}

/** The user's decision is part of the record: who applied it, when. */
export function DecidedBy({ p }: { p: Proposal }) {
  if (!p.applied || !p.applied_by) return null;
  const label =
    p.applied_by === "system"
      ? "applied automatically"
      : p.user_payload != null
        ? "accepted by you (edited)"
        : "accepted by you";
  return (
    <PanelTitleMuted>
      {label}
      {p.applied_at && ` · ${formatDateTime(p.applied_at)}`}
    </PanelTitleMuted>
  );
}

function WorkFold({ items, open = false }: { items: TranscriptItem[]; open?: boolean }) {
  // Count what the transcript SHOWS (shared predicate, AUDIT FS-11).
  const visible = items.filter(isRenderable);
  const toolCount = visible.filter((it) => it.role === "tool").length;
  const thinkingCount = visible.filter((it) => it.role === "thinking").length;
  const label =
    [
      toolCount > 0 && `${toolCount} tool call${toolCount !== 1 ? "s" : ""}`,
      thinkingCount > 0 &&
        `${thinkingCount} reasoning step${thinkingCount !== 1 ? "s" : ""}`,
    ]
      .filter(Boolean)
      .join(" · ") || `${visible.length} steps`;
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
  // Proposals being edited must not self-fold when a decision lands
  // (AUDIT FS-5).
  const [dirtyIds, setDirtyIds] = useState<Set<number>>(new Set());
  const markDirty = (id: number) => (d: boolean) =>
    setDirtyIds((prev) => {
      if (prev.has(id) === d) return prev;
      const next = new Set(prev);
      if (d) next.add(id);
      else next.delete(id);
      return next;
    });
  // retryScheduled: no fake pulse — the header badge carries the plan
  // and the failed attempt's transcript (if any) renders normally (FS-8).
  const { streaming, items, mine, summaryIdx } =
    deriveTurnView(step, proposals, live);

  // Walk chronologically; successful propose_* calls become their
  // proposal (matched by the id in the tool result, order fallback).
  const out: React.ReactNode[] = [];
  let fold: typeof items = [];
  let foldStart = 0; // index (in items) of the current fold's first item
  let cursor = 0;
  const consumed = new Set<number>();
  const flush = (liveTail = false) => {
    if (fold.length > 0) {
      out.push(
        // Keyed by CONTENT position (AUDIT FS-10): the items array only
        // appends during streaming, so a fold keeps its identity (and
        // the user's expand/collapse state) when the composition around
        // it shifts.
        <WorkFold key={`fold-${foldStart}`} items={fold} open={liveTail} />,
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
        defaultOpen={
          dirtyIds.has(p.id) ||
          (p.status !== "applied" && p.status !== "no_change")
        }
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
        <ProposalCard
          proposal={p}
          archived={archived}
          withHeader={false}
          onDirtyChange={markDirty(p.id)}
        />
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
      // The closing words are the agent speaking — plain prose, no box.
      out.push(
        <div key="summary" className="px-2 pt-1">
          <ProseBody content={item.content} />
        </div>,
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
    if (fold.length === 0) foldStart = idx;
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
          <span className="mr-1.5 inline-block size-2 animate-pulse rounded-full bg-info" />
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
      {/* Uniform header strip — click anywhere to fold the whole turn.
          A REAL button (AUDIT UI-U2/FS-14): keyboard-operable and
          aria-expanded for free. */}
      <button
        type="button"
        aria-expanded={!folded}
        className={cn(
          frameHeaderClass,
          "w-full cursor-pointer text-left select-none",
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
        <span
          className={cn(
            "text-[15px] font-semibold tracking-tight",
            collapsed && "font-medium text-muted-foreground/70",
          )}
        >
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
      </button>

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
              <OcrBody step={step} proposals={proposals} />
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
      <OcrTimingSummary result={step.result} />
    ) : null;
  const showControls =
    !archived &&
    (step.state === "succeeded" ||
      step.state === "failed" ||
      step.state === "cancelled" ||
      (step.state === "pending" && step.scheduled_at != null));
  if (!timing && !showControls) return null;
  return (
    <div className={frameFooterClass}>
      {timing}
      <span className="flex-1" />
      {showControls && <StepControls step={step} onChanged={onChanged} />}
    </div>
  );
}
