import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

const statusColors: Record<string, string> = {
  idle: "bg-emerald-100 text-emerald-800",
  running: "bg-blue-100 text-blue-800",
  failed: "bg-red-100 text-red-700",
  archived: "bg-zinc-100 text-zinc-500",
};

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded border border-zinc-200 bg-white px-4 py-2">
      <p className="text-lg font-semibold">{value}</p>
      <p className="text-xs text-zinc-500">{label}</p>
    </div>
  );
}

export default function Sessions() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["sessions"],
    queryFn: api.listSessions,
    refetchInterval: 5000,
  });
  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: api.getStats,
    refetchInterval: 5000,
  });

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">Analyses</h1>
      <p className="mb-4 text-sm text-zinc-500">
        Every agent run — including ones that failed or concluded that no changes
        are needed (which never show up in the review queue).
      </p>

      {stats && (
        <div className="mb-4 flex gap-3">
          <StatCard label="proposals to review" value={stats.pending_proposals} />
          <StatCard label="analyses in flight" value={stats.active_sessions} />
          <StatCard
            label="queued work"
            value={Object.values(stats.queue_pending).reduce((a, b) => a + b, 0)}
          />
          <StatCard label="active campaigns" value={stats.active_jobs} />
        </div>
      )}

      {isLoading && <p className="text-zinc-500">Loading…</p>}
      {error && <p className="text-red-600">{String(error)}</p>}
      {data && data.length === 0 && (
        <p className="rounded border border-dashed border-zinc-300 p-8 text-center text-zinc-500">
          No analyses yet.
        </p>
      )}

      <ul className="space-y-2">
        {data?.map((s) => (
          <li key={s.id} className="rounded border border-zinc-200 bg-white p-3 hover:border-emerald-400">
            <Link className="flex w-full items-center gap-3 text-left" to={`/sessions/${s.id}`}>
              <span className="font-mono text-sm text-zinc-400">#{s.id}</span>
              <span className="font-medium">
                {s.entity_type ? `${s.entity_type} ${s.entity_id}` : s.agent_kind}
              </span>
              <span className="truncate text-sm text-zinc-500">{s.title}</span>
              <span className="ml-auto flex items-center gap-2">
                {s.phase === "ocr_review" && (
                  <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800">
                    OCR review needed
                  </span>
                )}
                {s.proposal_count > 0 ? (
                  <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-xs text-emerald-700">
                    {s.proposal_count} proposal{s.proposal_count > 1 ? "s" : ""}
                  </span>
                ) : (
                  s.phase === "done" && (
                    <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-500">
                      no changes proposed
                    </span>
                  )
                )}
                <span
                  className={`rounded px-2 py-0.5 text-xs font-medium capitalize ${statusColors[s.status] ?? "bg-zinc-100"}`}
                >
                  {s.phase && s.phase !== "done" ? s.phase.replaceAll("_", " ") : s.status === "idle" ? "finished" : s.status}
                </span>
              </span>
            </Link>
            {s.error && (
              <p className="mt-1 rounded bg-red-50 p-2 font-mono text-xs text-red-700">
                {s.error}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
