import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { SessionList } from "../components/SessionList";

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded border border-zinc-200 bg-white px-4 py-2">
      <p className="text-lg font-semibold">{value}</p>
      <p className="text-xs text-zinc-500">{label}</p>
    </div>
  );
}

export default function Sessions() {
  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: api.getStats,
    refetchInterval: 5000,
  });

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">Analyses</h1>
      <p className="mb-4 text-sm text-zinc-500">
        Every agent session — including ones that failed or concluded that no changes
        are needed.
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

      <SessionList pageSize={5} showEntity={true} />
    </div>
  );
}
