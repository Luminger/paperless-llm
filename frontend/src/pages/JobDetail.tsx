import { Link, useParams } from "react-router-dom";
import { SessionTable } from "../components/SessionList";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/app/PageHeader";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../api";
import { keys } from "../lib/keys";
import { StatusBadge } from "../components/StatusBadge";
import { scopeLabel } from "./Jobs";

export default function JobDetail() {
  const { id } = useParams();
  const jobId = Number(id);
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
    refetchInterval: 5000,
  });

  if (error) return <ErrorNotice error={error} />;
  if (!job) return <LoadingState lines={4} />;

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
            <StatusBadge status={job.status} />
          </div>
        }
      />
      <p className="-mt-2 mb-4 text-sm text-muted-foreground">
        {job.done} ok, {job.failed} failed of {job.total}
      </p>
      <SessionTable sessions={job.sessions} showEntity={false} />
    </div>
  );
}
