import { useParams } from "react-router-dom";
import { SessionTable } from "../components/SessionList";
import { useQuery } from "@tanstack/react-query";
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

  if (error) return <ErrorNotice error={error} />;
  if (!job) return <LoadingState lines={4} />;

  return (
    <div>
      <PageHeader
        title={scopeLabel(job)}
        actions={<StatusBadge status={job.status} />}
      />
      <p className="-mt-2 mb-4 text-sm text-muted-foreground">
        {job.done} ok, {job.failed} failed of {job.total}
      </p>
      <SessionTable sessions={job.sessions} showEntity={false} />
    </div>
  );
}
