import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { StatusBadge } from "../components/StatusBadge";

export default function JobDetail() {
  const { id } = useParams();
  const jobId = Number(id);
  const { data: job, error } = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.getJob(jobId),
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "queued" || s === "running" ? 2000 : false;
    },
  });

  if (error) return <p className="text-red-600">{String(error)}</p>;
  if (!job) return <p className="text-zinc-500">Loading…</p>;

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">Job #{job.id}</h1>
      <p className="mb-4 text-sm text-zinc-400">
        {job.done} ok, {job.failed} failed of {job.total} · <StatusBadge status={job.status} />
      </p>
      <ul className="space-y-1">
        {job.sessions.map((s) => (
          <li
            key={s.id}
            className="flex items-center gap-3 rounded border border-zinc-100 bg-white p-2 text-sm"
          >
            <span className="flex-1">{s.title || `Session #${s.id}`}</span>
            <span className="text-xs text-zinc-400">{s.phase}</span>
            <StatusBadge status={s.status} />
            {s.phase === "ocr_review" && (
              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
                OCR review needed
              </span>
            )}
            <Link className="text-xs text-emerald-700 hover:underline" to={`/sessions/${s.id}`}>
              open
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
