import { useState, useEffect } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, ArrowRight, Check, Pencil, X } from "lucide-react";
import { ConnectionToast } from "@/components/app/ConnectionToast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../api";
import { keys } from "../lib/keys";
import { useSessionEvents } from "../hooks/useSessionEvents";
import { entityHref } from "./EntityPage";
import { StepCard } from "../features/session/StepCard";
import { NextTurnBox } from "../features/session/ContinueBox";

function ArchivedBanner() {
  return (
    <div className="mb-4 rounded-lg border bg-muted/60 p-3 text-sm text-muted-foreground">
      This session is <strong>archived</strong>: its proposals cannot be applied
      anymore, but applied changes can still be reverted (going back in time is
      always allowed).
    </div>
  );
}

/** Flow-through review: while walking a job's waiting sessions, a slim
 * bar keeps the position ("N waiting on you") and the one move that
 * matters — Next. Manual by design: a decision often makes the SAME
 * document continue with a follow-up proposal, so the user leaves when
 * this one is truly done. */
function JobFlowBar({ sessionId, jobId }: { sessionId: number; jobId: number }) {
  const navigate = useNavigate();
  const { data: job } = useQuery({
    queryKey: keys.job(jobId),
    queryFn: () => api.getJob(jobId),
  });
  const { data: attention } = useQuery({
    queryKey: keys.jobAttention(jobId, sessionId),
    queryFn: () => api.getJobAttention(jobId, sessionId),
    refetchInterval: 4000,
  });
  const label = job ? (job.params.label as string) || "job" : "job";
  const next = attention?.next_session_id;
  return (
    <div className="mb-4 flex items-center gap-3 rounded-lg border border-primary/25 bg-primary/5 px-3 py-2 text-sm">
      <span className="flex-1 text-muted-foreground">
        Reviewing <Link className="font-medium text-foreground hover:underline" to={`/jobs/${jobId}`}>{label}</Link>
        {attention != null && (
          <>
            {" — "}
            {attention.remaining === 0
              ? "nothing else is waiting on you"
              : `${attention.remaining} waiting on you`}
          </>
        )}
      </span>
      {next != null ? (
        <Button
          size="sm"
          variant="outline"
          onClick={() => navigate(`/sessions/${next}?flow=1`)}
        >
          Next
          <ArrowRight className="size-3.5" />
        </Button>
      ) : (
        <Button asChild size="sm" variant="outline">
          <Link to={`/jobs/${jobId}`}>Back to the job</Link>
        </Button>
      )}
    </div>
  );
}

function ArchiveToggle({
  sessionId,
  archived,
  onChanged,
}: {
  sessionId: number;
  archived: boolean;
  onChanged: () => void;
}) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: () =>
      archived ? api.unarchiveSession(sessionId) : api.archiveSession(sessionId),
    onSuccess: () => {
      onChanged();
      qc.invalidateQueries({ queryKey: keys.sessions() });
    },
  });
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={toggle.isPending}
      title={
        archived
          ? "Unarchive: allow applying proposals again"
          : "Archive: keeps history & revert, blocks new work and applies"
      }
      onClick={() => toggle.mutate()}
    >
      {archived ? (
        <>
          <ArchiveRestore className="size-3.5" /> Unarchive
        </>
      ) : (
        <>
          <Archive className="size-3.5" /> Archive
        </>
      )}
    </Button>
  );
}

/** The session's name is the user's — click the pencil, type, done. */
function EditableTitle({ id, title }: { id: number; title: string }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const rename = useMutation({
    mutationFn: () => api.renameSession(id, draft.trim()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.session(id) });
      qc.invalidateQueries({ queryKey: keys.sessions() });
      setEditing(false);
    },
  });
  if (!editing) {
    return (
      <span className="group flex items-center gap-2">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        <Button
          variant="ghost"
          size="icon"
          aria-label="rename session"
          className="size-7 text-muted-foreground opacity-0 transition group-hover:opacity-100"
          onClick={() => {
            setDraft(title);
            setEditing(true);
          }}
        >
          <Pencil className="size-3.5" />
        </Button>
      </span>
    );
  }
  return (
    <span className="flex items-center gap-2">
      <Input
        autoFocus
        aria-label="session name"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && draft.trim()) rename.mutate();
          if (e.key === "Escape") setEditing(false);
        }}
        className="h-8 w-72 text-lg font-semibold"
      />
      <Button
        size="icon"
        variant="ghost"
        className="size-7"
        aria-label="save name"
        disabled={!draft.trim() || rename.isPending}
        onClick={() => rename.mutate()}
      >
        <Check className="size-4" />
      </Button>
      <Button
        size="icon"
        variant="ghost"
        className="size-7"
        aria-label="cancel rename"
        onClick={() => setEditing(false)}
      >
        <X className="size-4" />
      </Button>
    </span>
  );
}

export default function SessionDetail() {
  const { id } = useParams();
  const sessionId = Number(id);
  const qc = useQueryClient();
  const [search] = useSearchParams();
  const inFlow = search.get("flow") != null;
  const { live, connected, nextRetryAt, pruneLive } = useSessionEvents(sessionId);
  const { data: s, error } = useQuery({
    queryKey: keys.session(sessionId),
    queryFn: () => api.getSession(sessionId),
    // SSE is the primary update channel; when it is down (buffering
    // reverse proxy, network hiccup) a slow poll keeps running
    // sessions from appearing frozen forever.
    refetchInterval: connected ? false : 10_000,
  });
  // State-driven live pruning (AUDIT FS-4): once the refetch shows a
  // step settled, its streamed items are superseded by the transcript.
  useEffect(() => {
    if (!s) return;
    pruneLive(
      s.steps
        .filter((st) => st.state === "running" || st.state === "pending")
        .map((st) => st.id),
    );
  }, [s, pruneLive]);

  if (error) return <ErrorNotice error={error} />;
  if (!s) return <LoadingState lines={6} />;

  const onChanged = () => qc.invalidateQueries({ queryKey: keys.session(sessionId) });
  const archived = s.archived_at != null;
  const busyStep = s.steps.find(
    (st) => st.state === "pending" || st.state === "running" || st.state === "awaiting_user",
  );
  // Free-text steering appears once nothing is pending — the next turn
  // is the user's decision, made in context at the end of the feed.
  const canContinue = !archived && busyStep == null;

  // Turns count up across the session's LIVE steps (superseded history
  // keeps its kind label and doesn't shift the numbering).
  const turnByStep = new Map<number, number>();
  let turnNo = 0;
  for (const st of s.steps) {
    if (st.kind === "ocr" || st.state === "superseded") continue;
    turnByStep.set(st.id, ++turnNo);
  }

  return (
    <div>
      {s.entity_type != null && s.entity_id != null && (
        <nav className="mb-2 text-sm">
          <Link
            className="text-primary hover:underline"
            to={entityHref(s.entity_type, s.entity_id)}
          >
            ← Back to the {s.entity_type === "document" ? "document" : s.entity_type.replaceAll("_", " ")}
          </Link>
        </nav>
      )}
      <div className="mb-4 flex items-center gap-3">
        <EditableTitle id={s.id} title={s.title} />
        {s.entity_name && (
          <span className="text-sm text-muted-foreground">· {s.entity_name}</span>
        )}
        <span className="flex-1" />
        <ArchiveToggle sessionId={s.id} archived={archived} onChanged={onChanged} />
      </div>
      {inFlow && s.job_id != null && (
        <JobFlowBar sessionId={s.id} jobId={s.job_id} />
      )}
      {archived && <ArchivedBanner />}

      <div className="space-y-3">
        {s.steps.map((step) => (
          <StepCard
            key={step.id}
            step={step}
            proposals={s.proposals}
            live={live[step.id]}
            onChanged={onChanged}
            archived={archived}
            turn={turnByStep.get(step.id)}
          />
        ))}
        {/* keyed by turn (AUDIT FS-7): a finished turn always yields a fresh box */}
        {canContinue && <NextTurnBox key={turnNo + 1} sessionId={s.id} turn={turnNo + 1} />}
      </div>

      {/* Connection state lives OUT of the content flow (the central
          toast style); it vanishes the moment the stream reconnects. */}
      <ConnectionToast
        show={!connected}
        label="Live updates unavailable (refreshing every 10s meanwhile)"
        nextRetryAt={nextRetryAt}
      />
    </div>
  );
}
