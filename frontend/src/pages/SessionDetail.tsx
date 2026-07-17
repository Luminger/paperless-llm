import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Proposal, type TranscriptItem } from "../api";
import { DiffView } from "../components/DiffView";
import { ProposalCard } from "../components/ProposalCard";
import { useSessionEvents } from "../hooks/useSessionEvents";

function Step({
  label,
  state,
  children,
}: {
  label: string;
  state: "done" | "active" | "waiting" | "pending" | "failed";
  children?: React.ReactNode;
}) {
  const dot = {
    done: "bg-emerald-500",
    active: "bg-blue-500 animate-pulse",
    waiting: "bg-amber-500",
    pending: "bg-zinc-300",
    failed: "bg-red-500",
  }[state];
  return (
    <li className="relative pb-6 pl-8 last:pb-0">
      <span className={`absolute top-1 left-0 h-3.5 w-3.5 rounded-full ${dot}`} />
      <span className="absolute top-5 bottom-0 left-[6px] w-px bg-zinc-200" />
      <p className="mb-2 font-medium">{label}</p>
      {children}
    </li>
  );
}

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
          </li>
        ))}
      </ul>
    </details>
  );
}

function OcrGate({ sessionId, onResolved }: { sessionId: number; onResolved: () => void }) {
  const { data: ocr } = useQuery({
    queryKey: ["session-ocr", sessionId],
    queryFn: () => api.getOcrReview(sessionId),
  });
  const [newText, setNewText] = useState<string | null>(null);
  const [rerunInstructions, setRerunInstructions] = useState("");
  const gate = useMutation({
    mutationFn: (content: string | null) => api.resolveOcrGate(sessionId, content),
    onSuccess: onResolved,
  });
  const rerun = useMutation({
    mutationFn: () => api.rerunOcr(sessionId, rerunInstructions.trim() || null),
    onSuccess: onResolved,
  });

  useEffect(() => {
    if (ocr && newText === null) setNewText(ocr.ocr_text);
  }, [ocr, newText]);

  if (!ocr || newText === null) return <p className="text-sm text-zinc-500">Loading OCR result…</p>;

  return (
    <div className="space-y-3">
      <p className="text-sm text-zinc-600">
        Review the re-OCRed content ({ocr.pages} page{ocr.pages !== 1 ? "s" : ""}). You can
        fix mistakes directly in the new text before accepting. The metadata analysis
        continues only after this step — based on whatever content you decide on.
      </p>
      <DiffView oldText={ocr.previous_content} newText={newText} onNewTextChange={setNewText} />
      <div className="flex gap-2">
        <button
          className="rounded bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-700"
          onClick={() => gate.mutate(newText)}
          disabled={gate.isPending || rerun.isPending}
        >
          Accept {newText !== ocr.ocr_text ? "(with your fixes) " : ""}& continue
        </button>
        <button
          className="rounded bg-zinc-200 px-3 py-1.5 text-sm hover:bg-zinc-300"
          onClick={() => gate.mutate(null)}
          disabled={gate.isPending || rerun.isPending}
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
            placeholder="e.g. transcribe the handwritten stamp in the corner too; the amounts are in the right column"
            value={rerunInstructions}
            onChange={(e) => setRerunInstructions(e.target.value)}
          />
          <button
            className="rounded bg-zinc-700 px-3 py-1.5 text-sm text-white hover:bg-zinc-800"
            onClick={() => rerun.mutate()}
            disabled={rerun.isPending || gate.isPending}
          >
            Re-run OCR
          </button>
        </div>
      </details>
      {gate.error && <p className="text-sm text-red-600">{String(gate.error)}</p>}
      {rerun.error && <p className="text-sm text-red-600">{String(rerun.error)}</p>}
    </div>
  );
}

function ChatItem({ item }: { item: TranscriptItem }) {
  if (item.role === "user") {
    return (
      <div className="ml-8 rounded-lg border border-emerald-200 bg-emerald-50 p-2 text-sm whitespace-pre-wrap">
        {item.content}
      </div>
    );
  }
  return (
    <div className="mr-8 rounded-lg border border-zinc-200 bg-white p-2 text-sm whitespace-pre-wrap text-zinc-700">
      {item.content}
    </div>
  );
}

function Conversation({
  sessionId,
  items,
  busy,
  error,
}: {
  sessionId: number;
  items: TranscriptItem[];
  busy: boolean;
  error: string | null;
}) {
  const [draft, setDraft] = useState("");
  const send = useMutation({
    mutationFn: (content: string) => api.sendMessage(sessionId, content),
    onSuccess: () => setDraft(""),
  });

  // Group consecutive tool calls into one collapsed trace.
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
      {blocks.map((b, i) =>
        Array.isArray(b) ? <ToolTrace key={i} items={b} /> : <ChatItem key={i} item={b} />,
      )}
      {busy && (
        <p className="text-sm text-zinc-500">
          <span className="mr-1 inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" />
          Agent is working…
        </p>
      )}
      {error && <p className="rounded bg-red-50 p-2 font-mono text-xs text-red-700">{error}</p>}
      <form
        className="flex gap-2 pt-1"
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
          disabled={busy}
        />
        <button
          type="submit"
          className="self-end rounded bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-700 disabled:opacity-50"
          disabled={busy || !draft.trim()}
        >
          Send
        </button>
      </form>
      {send.error && <p className="text-sm text-red-600">{String(send.error)}</p>}
    </div>
  );
}

function ProposalChain({ head, byId }: { head: Proposal; byId: Map<number, Proposal> }) {
  const ancestors: Proposal[] = [];
  let cur: Proposal | undefined = head;
  while (cur?.supersedes_id != null) {
    cur = byId.get(cur.supersedes_id);
    if (!cur) break;
    ancestors.push(cur);
  }
  return (
    <div className="rounded border border-zinc-200 bg-zinc-50 p-4">
      <ProposalCard proposal={head} />
      {ancestors.length > 0 && (
        <details className="mt-3 border-t border-zinc-200 pt-2">
          <summary className="cursor-pointer text-xs text-zinc-500 select-none">
            {ancestors.length} earlier revision{ancestors.length !== 1 ? "s" : ""} (superseded)
          </summary>
          <div className="mt-3 space-y-4 opacity-70">
            {ancestors.map((p) => (
              <div key={p.id} className="rounded border border-zinc-200 bg-white p-3">
                <p className="mb-2 text-xs text-zinc-400">Revision {p.revision}</p>
                <ProposalCard proposal={p} />
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

export default function SessionDetail() {
  const { id } = useParams();
  const sessionId = Number(id);
  const qc = useQueryClient();
  useSessionEvents(sessionId);
  const { data: s, error } = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => api.getSession(sessionId),
  });

  if (error) return <p className="text-red-600">{String(error)}</p>;
  if (!s) return <p className="text-zinc-500">Loading…</p>;

  const redoOcr = s.params.redo_ocr === true;
  const gateResolution = s.params.ocr_gate as string | undefined;
  const phase = s.phase ?? "done";
  const failed = s.status === "failed";
  const running = s.status === "running";

  // Split the transcript: everything up to (and including) the first
  // agent text is the initial analysis; the rest is conversation.
  const firstAgent = s.transcript.findIndex((t) => t.role === "agent");
  const analysisText = firstAgent >= 0 ? s.transcript[firstAgent].content : "";
  const analysisTools = s.transcript
    .slice(0, firstAgent < 0 ? s.transcript.length : firstAgent)
    .filter((t) => t.role === "tool");
  const chatItems = firstAgent >= 0 ? s.transcript.slice(firstAgent + 1) : [];

  const ocrState = !redoOcr
    ? null
    : failed && (phase === "queued" || phase === "ocr_running")
      ? ("failed" as const)
      : phase === "ocr_running" || phase === "queued"
      ? phase === "ocr_running"
        ? ("active" as const)
        : ("pending" as const)
      : phase === "ocr_review"
        ? ("waiting" as const)
        : ("done" as const);
  const failedDuringOcr = failed && redoOcr && (phase === "queued" || phase === "ocr_running");
  const analysisState = failedDuringOcr
    ? ("pending" as const)
    : failed
    ? ("failed" as const)
    : phase === "analyzing"
      ? ("active" as const)
      : phase === "done"
        ? ("done" as const)
        : ("pending" as const);

  const visibleProposals = s.proposals.filter((p) => p.kind !== "replace_content");
  const supersededIds = new Set(
    visibleProposals.map((p) => p.supersedes_id).filter((x): x is number => x != null),
  );
  const heads = visibleProposals.filter((p) => !supersededIds.has(p.id));
  const byId = new Map(visibleProposals.map((p) => [p.id, p]));

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">
        {s.entity_type === "document" ? `Document #${s.entity_id} — analysis` : s.title}
      </h1>
      <p className="mb-6 text-sm text-zinc-400">
        Session #{s.id} · started {new Date(s.created_at).toLocaleString()}
      </p>

      <ol>
        <Step label="Analysis requested" state="done">
          <div className="flex gap-2 text-xs">
            <span className="rounded bg-zinc-100 px-2 py-0.5 text-zinc-600">
              {redoOcr ? "re-do OCR: yes" : "re-do OCR: no"}
            </span>
            {typeof s.params.instructions === "string" && (
              <span className="rounded bg-zinc-100 px-2 py-0.5 text-zinc-600">
                “{s.params.instructions}”
              </span>
            )}
          </div>
        </Step>

        {redoOcr && ocrState && (
          <Step
            label={
              ocrState === "failed"
                ? "OCR failed"
                : ocrState === "waiting"
                  ? "OCR review — your input needed"
                  : ocrState === "active"
                    ? "OCR running…"
                    : gateResolution === "kept_existing"
                      ? "OCR reviewed — existing content kept"
                      : gateResolution === "accepted"
                        ? "OCR reviewed — new content accepted"
                        : "OCR"
            }
            state={ocrState}
          >
            {ocrState === "failed" && s.error && (
              <p className="rounded bg-red-50 p-2 font-mono text-xs text-red-700">{s.error}</p>
            )}
            {ocrState === "active" && typeof s.params.ocr_instructions === "string" && (
              <p className="text-xs text-zinc-500">
                Re-running with: “{s.params.ocr_instructions}”
              </p>
            )}
            {ocrState === "waiting" && (
              <OcrGate
                sessionId={s.id}
                onResolved={() => qc.invalidateQueries({ queryKey: ["session", sessionId] })}
              />
            )}
          </Step>
        )}

        <Step
          label={
            failed && !failedDuringOcr && phase !== "done"
              ? "Metadata analysis failed"
              : analysisState === "active"
                ? "Metadata analysis running…"
                : analysisState === "done"
                  ? "Metadata analysis"
                  : "Metadata analysis (pending)"
          }
          state={analysisState === "failed" && phase === "done" ? "done" : analysisState}
        >
          {failed && !failedDuringOcr && phase !== "done" && (
            <p className="rounded bg-red-50 p-2 font-mono text-xs text-red-700">{s.error}</p>
          )}
          {phase === "done" && (
            <div className="space-y-2">
              <ToolTrace items={analysisTools} />
              {analysisText && (
                <pre className="rounded border border-zinc-200 bg-white p-3 font-sans text-sm whitespace-pre-wrap text-zinc-700">
                  {analysisText}
                </pre>
              )}
            </div>
          )}
        </Step>

        {phase === "done" && (
          <Step label={heads.length > 0 ? "Proposals" : "No changes proposed"} state="done">
            <div className="space-y-6">
              {heads.map((p) => (
                <ProposalChain key={p.id} head={p} byId={byId} />
              ))}
            </div>
          </Step>
        )}

        {phase === "done" && (
          <Step label="Conversation" state={running ? "active" : "done"}>
            <Conversation
              sessionId={s.id}
              items={chatItems}
              busy={running}
              error={failed ? s.error : null}
            />
          </Step>
        )}
      </ol>
    </div>
  );
}
