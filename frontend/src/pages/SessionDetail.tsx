import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type CallTiming,
  type Proposal,
  type Step,
  type TranscriptItem,
} from "../api";
import { DiffView } from "../components/DiffView";
import { ProposalCard } from "../components/ProposalCard";
import { useSessionEvents, type LiveActivity } from "../hooks/useSessionEvents";
import { entityHref } from "./EntityPage";

// ---------------------------------------------------------------------
// Generic pieces shared by every step kind: state dot, timing chips,
// attempt history, retry/redo controls, live progress. Kind-specific
// code renders only the BODY of a step.
// ---------------------------------------------------------------------

function timingLabel(t: CallTiming): string {
  const parts = [`${t.duration_s.toFixed(1)}s`];
  if (t.tps != null) parts.push(`${t.tps.toFixed(0)} tok/s`);
  if (t.ttft_s != null) parts.push(`ttft ${t.ttft_s.toFixed(2)}s`);
  return parts.join(" · ");
}

function TimingChip({ t }: { t: CallTiming | null }) {
  if (!t) return null;
  return (
    <span
      className="text-[10px] text-zinc-400"
      title={`${t.started_at} → ${t.finished_at}, ${t.input_tokens ?? "?"} in / ${t.output_tokens ?? "?"} out tokens`}
    >
      {timingLabel(t)}
    </span>
  );
}

const STATE_DOT: Record<Step["state"], string> = {
  pending: "bg-zinc-300",
  running: "bg-blue-500 animate-pulse",
  awaiting_user: "bg-amber-500",
  succeeded: "bg-emerald-500",
  failed: "bg-red-500",
  superseded: "bg-zinc-200",
  cancelled: "bg-zinc-300",
};

function clock(ts: string | null | undefined): string {
  return ts ? new Date(ts).toLocaleTimeString() : "";
}

function AttemptHistory({ step }: { step: Step }) {
  const finished = step.attempts.filter((a) => a.attempt != null);
  if (finished.length <= 1 && !step.attempts.some((a) => a.manual_retry_at)) return null;
  return (
    <ol className="space-y-0.5 text-xs text-zinc-500">
      {step.attempts.map((a, i) =>
        a.manual_retry_at ? (
          <li key={i} className="text-zinc-400">
            manual retry requested · {clock(a.manual_retry_at)}
          </li>
        ) : (
          <li key={i}>
            <span className="font-medium">Attempt {a.attempt}</span>
            {a.started_at && ` · ${clock(a.started_at)}`}
            {a.finished_at ? ` → ${clock(a.finished_at)}` : " → interrupted"}
            {a.error ? (
              <span className="text-red-600"> · {a.error.slice(0, 140)}</span>
            ) : (
              <span className="text-emerald-600"> · ok</span>
            )}
          </li>
        ),
      )}
    </ol>
  );
}

// Which input fields a redo may amend, per step kind.
const REDO_FIELDS: Record<Step["kind"], { key: string; label: string; long?: boolean }[]> = {
  ocr: [
    { key: "instructions", label: "OCR instructions" },
    { key: "dpi", label: "render DPI" },
  ],
  analysis: [{ key: "instructions", label: "instructions for the agent" }],
  chat: [{ key: "content", label: "message", long: true }],
};

function RedoDialog({
  step,
  onConfirm,
  onCancel,
  busy,
}: {
  step: Step;
  onConfirm: (input: Record<string, unknown>) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const fields = REDO_FIELDS[step.kind];
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      fields.map((f) => [f.key, step.input[f.key] != null ? String(step.input[f.key]) : ""]),
    ),
  );
  return (
    <div className="space-y-2 rounded border border-amber-200 bg-amber-50/60 p-3 text-sm">
      <p className="text-xs text-amber-800">
        Redoing this step supersedes it <strong>and every step after it</strong> —
        later results (including open proposals) were based on it. Adjust how the
        redo should run:
      </p>
      {fields.map((f) =>
        f.long ? (
          <textarea
            key={f.key}
            aria-label={`redo ${f.label}`}
            className="w-full rounded border border-zinc-300 p-2 text-sm"
            rows={2}
            placeholder={f.label}
            value={values[f.key]}
            onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
          />
        ) : (
          <label key={f.key} className="flex items-center gap-2 text-xs text-zinc-600">
            <span className="w-40">{f.label}</span>
            <input
              aria-label={`redo ${f.label}`}
              className="flex-1 rounded border border-zinc-300 px-2 py-1 text-sm"
              value={values[f.key]}
              onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
            />
          </label>
        ),
      )}
      <div className="flex gap-2">
        <button
          className="rounded bg-amber-600 px-3 py-1 text-xs text-white hover:bg-amber-700 disabled:opacity-50"
          disabled={busy}
          onClick={() => {
            const input: Record<string, unknown> = {};
            for (const f of fields) {
              const raw = values[f.key].trim();
              if (raw === "") continue;
              input[f.key] = f.key === "dpi" ? Number(raw) : raw;
            }
            onConfirm(input);
          }}
        >
          Redo step
        </button>
        <button
          className="rounded bg-zinc-200 px-3 py-1 text-xs hover:bg-zinc-300"
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    </div>
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
    <div className="space-y-2">
      <div className="flex items-center gap-3 text-xs">
        {scheduled && (
          <span className="text-zinc-500">
            Automatic retry {retriesDone + 1} of {maxRetries} at {clock(step.scheduled_at)}
          </span>
        )}
        {step.state === "failed" && maxRetries > 0 && (
          <span className="text-zinc-500">
            all {maxRetries} automatic retr{maxRetries !== 1 ? "ies" : "y"} used
          </span>
        )}
        {/* Manual retries are never limited. */}
        {(step.state === "failed" || scheduled || step.state === "cancelled") && (
          <button
            className="rounded bg-zinc-700 px-2 py-1 text-white hover:bg-zinc-800 disabled:opacity-50"
            onClick={() => retry.mutate()}
            disabled={retry.isPending}
          >
            Retry now
          </button>
        )}
        {(step.state === "succeeded" || step.state === "failed") && !redoOpen && (
          <button
            className="rounded bg-zinc-200 px-2 py-1 text-zinc-700 hover:bg-zinc-300"
            onClick={() => setRedoOpen(true)}
            title="Do this step over with adjusted parameters"
          >
            Redo…
          </button>
        )}
        {retry.error && <span className="text-red-600">{String(retry.error)}</span>}
        {redo.error && <span className="text-red-600">{String(redo.error)}</span>}
      </div>
      {redoOpen && (
        <RedoDialog
          step={step}
          busy={redo.isPending}
          onConfirm={(input) => redo.mutate(input)}
          onCancel={() => setRedoOpen(false)}
        />
      )}
    </div>
  );
}

function LiveTrace({ live }: { live: LiveActivity | undefined }) {
  if (!live || (live.tools.length === 0 && live.tokens === 0)) return null;
  return (
    <div className="space-y-1 rounded border border-blue-100 bg-blue-50/50 p-2 text-xs">
      {live.tools.map((t, i) => (
        <p key={i} className="font-mono text-zinc-600">
          → {t.tool}
          {t.args && t.args !== "{}" && <span className="text-zinc-400"> {t.args}</span>}
        </p>
      ))}
      {live.tokens > 0 && (
        <p className="text-zinc-500">
          <span className="mr-1 inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" />
          streaming… {live.tokens} tokens
        </p>
      )}
      {live.textTail && (
        <p className="font-mono text-[11px] whitespace-pre-wrap text-zinc-400">
          …{live.textTail.slice(-200)}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------
// Transcript rendering (agent-turn step bodies).
// ---------------------------------------------------------------------

function ToolTrace({ items }: { items: TranscriptItem[] }) {
  if (items.length === 0) return null;
  return (
    <details className="rounded border border-zinc-200 bg-zinc-50 px-2 py-1 text-xs text-zinc-500">
      <summary className="cursor-pointer select-none">
        {items.length} tool call{items.length !== 1 ? "s" : ""}
      </summary>
      <ul className="mt-1 space-y-1 font-mono">
        {items.map((t, i) => (
          <li key={i}>
            <span className="text-zinc-700">{t.tool_name}</span>
            {t.tool_args && Object.keys(t.tool_args).length > 0 && (
              <span> {JSON.stringify(t.tool_args).slice(0, 120)}</span>
            )}
            {t.tool_result?.startsWith("rejected: ") && (
              <span className="text-amber-600"> → {t.tool_result.slice(0, 120)}</span>
            )}
            {t.timing && <span className="text-zinc-400"> · {timingLabel(t.timing)}</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}

function Transcript({ items }: { items: TranscriptItem[] }) {
  const blocks: (TranscriptItem | TranscriptItem[])[] = [];
  for (const item of items) {
    const last = blocks[blocks.length - 1];
    if (item.role === "tool") {
      if (Array.isArray(last)) last.push(item);
      else blocks.push([item]);
    } else {
      blocks.push(item);
    }
  }
  return (
    <div className="space-y-2">
      {blocks.map((b, i) => {
        if (Array.isArray(b)) return <ToolTrace key={i} items={b} />;
        if (b.role === "user") {
          if (b.origin === "pipeline") return null; // synthetic kickoff
          return (
            <div key={i} className="ml-8 rounded-lg border border-emerald-200 bg-emerald-50 p-2 text-sm whitespace-pre-wrap">
              {b.content}
            </div>
          );
        }
        return (
          <div key={i} className="mr-8 rounded-lg border border-zinc-200 bg-white p-2 text-sm text-zinc-700">
            <div className="whitespace-pre-wrap">{b.content}</div>
            {b.timing && (
              <div className="mt-1 text-right">
                <TimingChip t={b.timing} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------
// Kind-specific bodies.
// ---------------------------------------------------------------------

function OcrGateBody({ step }: { step: Step }) {
  const qc = useQueryClient();
  const { data: ocr } = useQuery({
    queryKey: ["session-ocr", step.session_id],
    queryFn: () => api.getOcrReview(step.session_id),
  });
  const [newText, setNewText] = useState<string | null>(null);
  const [rerunInstructions, setRerunInstructions] = useState("");
  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ["session", step.session_id] });
  const resolve = useMutation({
    mutationFn: (content: string | null) =>
      api.resolveStep(step.session_id, step.id, content),
    onSuccess: invalidate,
  });
  const redo = useMutation({
    mutationFn: () =>
      api.redoStep(step.session_id, step.id, {
        instructions: rerunInstructions.trim() || undefined,
      }),
    onSuccess: invalidate,
  });

  useEffect(() => {
    if (ocr && newText === null) setNewText(ocr.ocr_text);
  }, [ocr, newText]);

  if (!ocr || newText === null)
    return <p className="text-sm text-zinc-500">Loading OCR result…</p>;

  return (
    <div className="space-y-3">
      <p className="text-sm text-zinc-600">
        Review the re-OCRed content. You can fix mistakes directly in the new text
        before accepting. The analysis continues only after this step — based on
        whatever content you decide on.
      </p>
      <DiffView oldText={ocr.previous_content} newText={newText} onNewTextChange={setNewText} />
      <div className="flex gap-2">
        <button
          className="rounded bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-700"
          onClick={() => resolve.mutate(newText)}
          disabled={resolve.isPending || redo.isPending}
        >
          Accept {newText !== ocr.ocr_text ? "(with your fixes) " : ""}& continue
        </button>
        <button
          className="rounded bg-zinc-200 px-3 py-1.5 text-sm hover:bg-zinc-300"
          onClick={() => resolve.mutate(null)}
          disabled={resolve.isPending || redo.isPending}
        >
          Keep existing content & continue
        </button>
      </div>
      <details className="rounded border border-zinc-200 bg-zinc-50 p-2">
        <summary className="cursor-pointer text-sm text-zinc-600 select-none">
          Not happy with the OCR? Re-run it with instructions
        </summary>
        <div className="mt-2 space-y-2">
          <textarea
            aria-label="re-run instructions"
            className="w-full rounded border border-zinc-300 p-2 text-sm"
            rows={2}
            placeholder="e.g. transcribe the handwritten stamp in the corner too"
            value={rerunInstructions}
            onChange={(e) => setRerunInstructions(e.target.value)}
          />
          <button
            className="rounded bg-zinc-700 px-3 py-1.5 text-sm text-white hover:bg-zinc-800"
            onClick={() => redo.mutate()}
            disabled={redo.isPending || resolve.isPending}
          >
            Re-run OCR
          </button>
        </div>
      </details>
      {resolve.error && <p className="text-sm text-red-600">{String(resolve.error)}</p>}
      {redo.error && <p className="text-sm text-red-600">{String(redo.error)}</p>}
    </div>
  );
}

function OcrBody({ step }: { step: Step }) {
  if (step.state === "awaiting_user") return <OcrGateBody step={step} />;
  const resolution = step.result.resolution as string | undefined;
  const pages = step.result.pages as number | undefined;
  const duration = step.result.duration_s as number | undefined;
  return (
    <div className="space-y-1 text-sm text-zinc-600">
      {typeof step.input.instructions === "string" && (
        <p className="text-xs text-zinc-500">with instructions: “{step.input.instructions}”</p>
      )}
      {pages != null && step.state !== "pending" && step.state !== "running" && (
        <p className="text-xs text-zinc-500">
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
          <details key={p.id} className="rounded border border-zinc-200 bg-zinc-50 p-2">
            <summary className="cursor-pointer text-xs text-zinc-500 select-none">
              Proposal #{p.id} rev {p.revision} — superseded by a newer revision
            </summary>
            <div className="mt-2 opacity-70">
              <ProposalCard proposal={p} archived={archived} />
            </div>
          </details>
        ) : (
          <div key={p.id} className="rounded border border-zinc-200 bg-zinc-50 p-4">
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
        <p className="text-sm text-zinc-500">No changes proposed.</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------
// The generic StepCard + the chronological feed.
// ---------------------------------------------------------------------

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
    <details className="rounded border border-zinc-200 bg-zinc-50/50 p-2">
      <summary className="cursor-pointer text-xs text-zinc-400 select-none">
        {paramsSummary(step)} — superseded, expand to inspect
      </summary>
      <div className="mt-3 space-y-3 opacity-80">
        {step.kind === "ocr" ? (
          text != null ? (
            <DiffView oldText={prev ?? ""} newText={text} />
          ) : (
            <p className="text-xs text-zinc-400">no OCR output recorded for this run</p>
          )
        ) : (
          <TurnBody step={step} proposals={proposals} archived={true} />
        )}
        <AttemptHistory step={step} />
      </div>
    </details>
  );
}

function StepCard({
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
      <span className={`absolute top-1 left-0 h-3.5 w-3.5 rounded-full ${STATE_DOT[step.state]}`} />
      <span className="absolute top-5 bottom-0 left-[6px] w-px bg-zinc-200" />
      <div className="mb-2 flex items-baseline gap-2">
        <p className={`font-medium ${collapsed ? "text-zinc-400" : ""}`}>{stepTitle(step)}</p>
        <span className="text-xs text-zinc-400">
          {clock(step.started_at ?? step.created_at)}
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
            <p className="rounded bg-red-50 p-2 font-mono text-xs text-red-700">{step.error}</p>
          )}
          {!archived && <StepControls step={step} onChanged={onChanged} />}
        </div>
      )}
    </li>
  );
}

function ArchivedBanner({ sessionId, onChanged }: { sessionId: number; onChanged: () => void }) {
  const unarchive = useMutation({
    mutationFn: () => api.unarchiveSession(sessionId),
    onSuccess: onChanged,
  });
  return (
    <div className="mb-4 flex items-center gap-3 rounded border border-zinc-300 bg-zinc-100 p-3 text-sm text-zinc-600">
      <span className="flex-1">
        This session is <strong>archived</strong>: its proposals cannot be applied
        anymore, but applied changes can still be reverted (going back in time is
        always allowed).
      </span>
      <button
        className="rounded bg-zinc-700 px-3 py-1.5 text-xs text-white hover:bg-zinc-800"
        onClick={() => unarchive.mutate()}
        disabled={unarchive.isPending}
      >
        Unarchive
      </button>
    </div>
  );
}

function Composer({ sessionId, disabled, hint }: { sessionId: number; disabled: boolean; hint: string | null }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const send = useMutation({
    mutationFn: (content: string) => api.sendMessage(sessionId, content),
    onSuccess: () => {
      setDraft("");
      qc.invalidateQueries({ queryKey: ["session", sessionId] });
    },
  });
  return (
    <div className="mt-2 border-t border-zinc-200 pt-4">
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (draft.trim()) send.mutate(draft.trim());
        }}
      >
        <textarea
          aria-label="steer the agent"
          className="min-h-10 flex-1 rounded border border-zinc-300 p-2 text-sm"
          rows={2}
          placeholder="Steer the agent: ask questions, request changes to the proposals…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={disabled}
        />
        <button
          type="submit"
          className="self-end rounded bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-700 disabled:opacity-50"
          disabled={disabled || !draft.trim()}
        >
          Send
        </button>
      </form>
      {hint && <p className="mt-1 text-xs text-zinc-400">{hint}</p>}
      {send.error && <p className="mt-1 text-sm text-red-600">{String(send.error)}</p>}
    </div>
  );
}

export default function SessionDetail() {
  const { id } = useParams();
  const sessionId = Number(id);
  const qc = useQueryClient();
  const { live } = useSessionEvents(sessionId);
  const { data: s, error } = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => api.getSession(sessionId),
  });

  if (error) return <p className="text-red-600">{String(error)}</p>;
  if (!s) return <p className="text-zinc-500">Loading…</p>;

  const onChanged = () => qc.invalidateQueries({ queryKey: ["session", sessionId] });
  const archived = s.archived_at != null;
  const busyStep = s.steps.find(
    (st) => st.state === "pending" || st.state === "running" || st.state === "awaiting_user",
  );
  const composerHint = archived
    ? "This session is archived — unarchive it to continue."
    : busyStep
      ? busyStep.state === "awaiting_user"
        ? "Resolve the step above first."
        : "A step is still running — you can steer once it finishes."
      : null;

  return (
    <div>
      {s.entity_type != null && s.entity_id != null && (
        <nav className="mb-2 text-sm">
          <Link
            className="text-emerald-700 hover:underline"
            to={entityHref(s.entity_type, s.entity_id)}
          >
            ← {s.entity_type === "document" ? "Document" : s.entity_type.replaceAll("_", " ")}{" "}
            #{s.entity_id}
          </Link>
        </nav>
      )}
      <h1 className="mb-1 text-xl font-semibold">
        {s.entity_type === "document" ? `Document #${s.entity_id} — analysis` : s.title}
      </h1>
      <p className="mb-2 text-sm text-zinc-400">
        Session #{s.id} · started {new Date(s.created_at).toLocaleString()}
        {s.params.redo_ocr === true && " · with OCR review"}
        {typeof s.params.instructions === "string" && ` · “${s.params.instructions}”`}
      </p>
      {archived && <ArchivedBanner sessionId={s.id} onChanged={onChanged} />}
      <div className="mb-4" />

      <ol>
        {s.steps.map((step) => (
          <StepCard
            key={step.id}
            step={step}
            proposals={s.proposals}
            live={live[step.id]}
            onChanged={onChanged}
            archived={archived}
          />
        ))}
      </ol>

      <Composer
        sessionId={s.id}
        disabled={busyStep != null || archived}
        hint={composerHint}
      />
    </div>
  );
}
