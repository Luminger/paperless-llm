import { Link, useParams } from "react-router-dom";
import { SessionTable } from "../components/SessionList";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/app/ConfirmDialog";
import { PageHeader } from "@/components/app/PageHeader";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../api";
import { keys } from "../lib/keys";
import { StatusBadge } from "../components/StatusBadge";
import { scopeLabel } from "./Jobs";

export default function JobDetail() {
  const { id } = useParams();
  const jobId = Number(id);
  const qc = useQueryClient();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const { data: job, error } = useQuery({
    queryKey: keys.job(jobId),
    queryFn: () => api.getJob(jobId),
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "queued" || s === "running" ? 2000 : false;
    },
  });
  // Flow-through review: one button that opens the first session
  // waiting on the user; each session's flow bar carries on from there.
  const { data: attention } = useQuery({
    queryKey: keys.jobAttention(jobId),
    queryFn: () => api.getJobAttention(jobId),
    // Poll only while there is anything to wait for (AUDIT FP-L6) — a
    // finished job left open in a tab must not hit the API forever.
    refetchInterval: (q) => {
      const active = job?.status === "queued" || job?.status === "running";
      const remaining = (q.state.data?.remaining ?? 0) > 0;
      return active || remaining ? 5000 : false;
    },
  });

  if (error) return <ErrorNotice error={error} />;
  if (!job) return <LoadingState lines={4} />;

  const cancellable = job.status === "queued" || job.status === "running";

  return (
    <div>
      <PageHeader
        title={scopeLabel(job)}
        actions={
          <div className="flex items-center gap-3">
            {attention?.next_session_id != null && (
              <Button asChild size="sm">
                <Link to={`/sessions/${attention.next_session_id}?flow=1`}>
                  Review {attention.remaining} waiting
                </Link>
              </Button>
            )}
            {cancellable && (
              <Button variant="secondary" size="sm" onClick={() => setConfirmCancel(true)}>
                Cancel job
              </Button>
            )}
            <StatusBadge status={job.status} />
          </div>
        }
      />
      <CancelJobDialog
        job={job}
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        onDone={() => qc.invalidateQueries({ queryKey: keys.job(jobId) })}
      />
      <p className="-mt-2 mb-4 text-sm text-muted-foreground">
        {job.done} ok, {job.failed} failed of {job.total}
      </p>
      <SessionTable sessions={job.sessions} showEntity={false} />
    </div>
  );
}


function CancelJobDialog({
  job,
  open,
  onOpenChange,
  onDone,
}: {
  job: { id: number };
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onDone: () => void;
}) {
  const cancel = useMutation({
    mutationFn: () => api.cancelJob(job.id),
    onSuccess: () => {
      onDone();
      onOpenChange(false);
    },
  });
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      error={cancel.error}
      title="Cancel this job?"
      description="Pending sessions will be cancelled; running steps finish and keep their results. Already-applied changes stay (revertible from the journal)."
      confirmLabel="Cancel the job"
      busy={cancel.isPending}
      onConfirm={() => cancel.mutate()}
    />
  );
}