import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../api";
import { keys } from "../lib/keys";
import { formatDateTime } from "../lib/format";
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
  const { live } = useSessionEvents(sessionId);
  const { data: s, error } = useQuery({
    queryKey: keys.session(sessionId),
    queryFn: () => api.getSession(sessionId),
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
            ← {s.entity_type === "document" ? "Document" : s.entity_type.replaceAll("_", " ")}{" "}
            #{s.entity_id}
          </Link>
        </nav>
      )}
      <h1 className="mb-1 text-xl font-semibold tracking-tight">
        {s.entity_type === "document" ? `Document #${s.entity_id} — analysis` : s.title}
      </h1>
      <p className="mb-4 text-sm text-muted-foreground/70">
        Session #{s.id} · started {formatDateTime(s.created_at)}
        {s.params.redo_ocr === true && " · with OCR review"}
        {typeof s.params.instructions === "string" && ` · “${s.params.instructions}”`}
      </p>
      {archived && <ArchivedBanner sessionId={s.id} onChanged={onChanged} />}

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
        {canContinue && <ContinueBox sessionId={s.id} />}
      </ol>
    </div>
  );
}
