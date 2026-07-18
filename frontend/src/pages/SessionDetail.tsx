import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../api";
import { keys } from "../lib/keys";
import { useSessionEvents } from "../hooks/useSessionEvents";
import { entityHref } from "./EntityPage";
import { StepCard } from "../features/session/StepCard";
import { NextTurnBox } from "../features/session/ContinueBox";

function ArchivedBanner({
  sessionId,
  onChanged,
}: {
  sessionId: number;
  onChanged: () => void;
}) {
  const unarchive = useMutation({
    mutationFn: () => api.unarchiveSession(sessionId),
    onSuccess: onChanged,
  });
  return (
    <div className="mb-4 flex items-center gap-3 rounded-lg border bg-muted/60 p-3 text-sm text-muted-foreground">
      <span className="flex-1">
        This session is <strong>archived</strong>: its proposals cannot be applied
        anymore, but applied changes can still be reverted (going back in time is
        always allowed).
      </span>
      <Button
        variant="secondary"
        size="sm"
        onClick={() => unarchive.mutate()}
        disabled={unarchive.isPending}
      >
        Unarchive
      </Button>
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

export default function SessionDetail() {
  const { id } = useParams();
  const sessionId = Number(id);
  const qc = useQueryClient();
  const [search] = useSearchParams();
  const inFlow = search.get("flow") != null;
  const { live, connected } = useSessionEvents(sessionId);
  const { data: s, error } = useQuery({
    queryKey: keys.session(sessionId),
    queryFn: () => api.getSession(sessionId),
    // SSE is the primary update channel; when it is down (buffering
    // reverse proxy, network hiccup) a slow poll keeps running
    // sessions from appearing frozen forever.
    refetchInterval: connected ? false : 10_000,
  });

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
      <h1 className="mb-4 text-xl font-semibold tracking-tight">{s.title}</h1>
      {inFlow && s.job_id != null && (
        <JobFlowBar sessionId={s.id} jobId={s.job_id} />
      )}
      {archived && <ArchivedBanner sessionId={s.id} onChanged={onChanged} />}

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
        {canContinue && <NextTurnBox sessionId={s.id} turn={turnNo + 1} />}
      </div>

      {/* Connection state lives OUT of the content flow; it vanishes
          the moment the event stream reconnects. */}
      {!connected && (
        <div className="fixed right-4 bottom-4 z-50 flex items-center gap-2 rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-800 shadow-md dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
          <span className="inline-block size-2 animate-pulse rounded-full bg-amber-500" />
          Live updates unavailable — reconnecting… (refreshing every 10s meanwhile)
        </div>
      )}
    </div>
  );
}
