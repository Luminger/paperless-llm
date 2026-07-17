import { useQuery } from "@tanstack/react-query";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader } from "@/components/app/PageHeader";
import { api } from "../api";
import { keys } from "../lib/keys";
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
    <Card className="py-3">
      <CardContent className="px-4">
        <p className="text-lg font-semibold">{value}</p>
        <p className="text-xs text-muted-foreground">{label}</p>
      </CardContent>
    </Card>
  );
}

export default function Dashboard() {
  const { data: stats } = useQuery({
    queryKey: keys.stats(),
    queryFn: api.getStats,
    refetchInterval: 5000,
  });
  const lifetime = stats?.lifetime ?? {};

  return (
    <div>
      <PageHeader title="Dashboard" />
      <p className="-mt-2 mb-4 text-sm text-muted-foreground">
        Sessions that still need something — your input at a gate, running work, or a
        look at a failure. Finished sessions live on their document's or entity's page.
      </p>

      {stats && (
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <StatCard label="proposals to review" value={stats.pending_proposals} />
          <StatCard label="analyses in flight" value={stats.active_sessions} />
          <StatCard
            label="queued work"
            value={Object.values(stats.queue_pending).reduce((a, b) => a + b, 0)}
          />
          <StatCard label="active jobs" value={stats.active_jobs} />
          <StatCard label="OCR runs (lifetime)" value={fmt(lifetime.ocr_runs)} />
          <StatCard
            label="LLM tokens generated (lifetime)"
            value={fmt(lifetime.llm_output_tokens)}
          />
        </div>
      )}

      <h2 className="mb-2 text-sm font-medium text-muted-foreground">Needs attention</h2>
      <SessionList unfinished pageSize={5} showEntity showArchived={false} />
    </div>
  );
}
