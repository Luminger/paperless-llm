import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { SessionList } from "../components/SessionList";

function fmt(n: number | undefined): string {
  if (n == null) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(0)}k`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded border border-zinc-200 bg-white px-4 py-2">
      <p className="text-lg font-semibold">{value}</p>
      <p className="text-xs text-zinc-500">{label}</p>
    </div>
  );
}

export default function Dashboard() {
  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: api.getStats,
    refetchInterval: 5000,
  });
  const lifetime = stats?.lifetime ?? {};

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">Dashboard</h1>
      <p className="mb-4 text-sm text-zinc-500">
        Sessions that still need something — your input at a gate, running work, or a
        look at a failure. Finished sessions live on their document's or entity's page.
      </p>

      {stats && (
        <div className="mb-6 flex flex-wrap gap-3">
          <StatCard label="proposals to review" value={stats.pending_proposals} />
          <StatCard label="analyses in flight" value={stats.active_sessions} />
          <StatCard
            label="queued work"
            value={Object.values(stats.queue_pending).reduce((a, b) => a + b, 0)}
          />
          <StatCard label="active campaigns" value={stats.active_jobs} />
          <StatCard label="OCR runs (lifetime)" value={fmt(lifetime.ocr_runs)} />
          <StatCard
            label="LLM tokens generated (lifetime)"
            value={fmt(lifetime.llm_output_tokens)}
          />
        </div>
      )}

      <h2 className="mb-2 text-sm font-medium text-zinc-600">Needs attention</h2>
      <SessionList unfinished pageSize={5} showEntity showArchived={false} />
    </div>
  );
}
