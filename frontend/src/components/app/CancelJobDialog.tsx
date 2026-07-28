// THE job-cancel guard. Jobs list and job page used to carry two
// copies whose descriptions drifted apart (one claimed running steps
// finish; the backend aborts them). One dialog, one truth: pending
// sessions are cancelled, running steps aborted, applied changes stay
// revertible.

import { useMutation } from "@tanstack/react-query";
import { ConfirmDialog } from "./ConfirmDialog";
import { api, type Job } from "../../api";
import { scopeLabel } from "../../lib/labels";

export function CancelJobDialog({
  job,
  open,
  onOpenChange,
  onDone,
}: {
  job: Job | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  /** Invalidate whatever the caller shows — runs after a successful cancel. */
  onDone: () => void;
}) {
  const cancel = useMutation({
    mutationFn: (id: number) => api.cancelJob(id),
    onSuccess: () => {
      onDone();
      onOpenChange(false);
    },
  });
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) cancel.reset(); // a stale error must not haunt the next dialog
        onOpenChange(next);
      }}
      error={cancel.error}
      title="Cancel this job?"
      description={
        job
          ? `"${scopeLabel(job)}" — pending sessions are cancelled and running steps aborted. Already-applied changes stay (revertible from the journal).`
          : ""
      }
      confirmLabel="Cancel the job"
      busy={cancel.isPending}
      onConfirm={() => job && cancel.mutate(job.id)}
    />
  );
}
