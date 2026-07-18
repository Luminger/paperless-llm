import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../api";
import { keys } from "../lib/keys";
import { useSessionEvents } from "../hooks/useSessionEvents";
import { entityHref } from "./EntityPage";
import { StepCard } from "../features/session/StepCard";
import { ContinueBox } from "../features/session/ContinueBox";

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

export default function SessionDetail() {
  const { id } = useParams();
  const sessionId = Number(id);
  const qc = useQueryClient();
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
      {!connected && (
        <p className="mb-3 rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
          Live updates unavailable — refreshing every 10 seconds instead.
        </p>
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
          />
        ))}
        {canContinue && <ContinueBox sessionId={s.id} />}
      </div>
    </div>
  );
}
