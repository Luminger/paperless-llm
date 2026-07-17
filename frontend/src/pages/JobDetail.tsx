import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/app/PageHeader";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../api";
import { keys } from "../lib/keys";
import { StatusBadge } from "../components/StatusBadge";

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
        title={`Job #${job.id}`}
        actions={<StatusBadge status={job.status} />}
      />
      <p className="-mt-2 mb-4 text-sm text-muted-foreground">
        {job.done} ok, {job.failed} failed of {job.total}
      </p>
      <ul className="divide-y rounded-lg border bg-card">
        {job.sessions.map((s) => (
          <li key={s.id} className="flex items-center gap-3 p-2.5 text-sm">
            <span className="flex-1">{s.title || `Session #${s.id}`}</span>
            <span className="text-xs text-muted-foreground">{s.phase}</span>
            <StatusBadge status={s.status} />
            {s.phase === "ocr_review" && (
              <Badge className="bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                OCR review needed
              </Badge>
            )}
            <Link className="text-xs text-primary hover:underline" to={`/sessions/${s.id}`}>
              open
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
