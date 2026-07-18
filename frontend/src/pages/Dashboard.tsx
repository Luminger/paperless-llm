import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { PageHeader } from "@/components/app/PageHeader";
import { SimpleSelect } from "@/components/app/SimpleSelect";
import { ErrorNotice } from "@/components/app/states";
import { api } from "../api";
import { keys } from "../lib/keys";
import { SessionList } from "../components/SessionList";

/** Batch-by-batch corpus curation: how much of the archive has been
 * analyzed, and one button that always means "give me the next slice".
 * Early batches straighten the taxonomy; later ones get easier as
 * paperless's matching starts pre-assigning. */
function CorpusBlock() {
  const navigate = useNavigate();
  const [size, setSize] = useState("10");
  const { data } = useQuery({
    queryKey: keys.corpus(),
    queryFn: api.getCorpus,
    refetchInterval: 30_000,
  });
  const start = useMutation({
    mutationFn: () => api.createJob({ next_batch: Number(size) }),
    onSuccess: (job) => navigate(`/jobs/${job.id}`),
  });
  if (!data || data.total === 0) return null;
  const done = data.processed >= data.total;
  const pct = Math.round((data.processed / data.total) * 100);
  return (
    <Card className="mb-6 py-4">
      <CardContent className="px-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-56 flex-1">
            <p className="text-sm font-medium">Corpus</p>
            <div className="mt-1.5 flex items-center gap-3">
              <Progress value={pct} className="max-w-72 flex-1" />
              <p className="text-xs whitespace-nowrap text-muted-foreground">
                {data.processed.toLocaleString()} of {data.total.toLocaleString()}{" "}
                documents analyzed
              </p>
            </div>
          </div>
          {done ? (
            <p className="text-sm text-muted-foreground">
              Every document has been analyzed.
            </p>
          ) : (
            <div className="flex items-center gap-2">
              <SimpleSelect
                ariaLabel="batch size"
                value={size}
                onValueChange={setSize}
                options={["10", "25", "50"].map((n) => ({
                  value: n,
                  label: `${n} documents`,
                }))}
              />
              <Button
                size="sm"
                disabled={start.isPending}
                onClick={() => start.mutate()}
              >
                Analyze next batch
              </Button>
            </div>
          )}
        </div>
        <ErrorNotice error={start.error} />
      </CardContent>
    </Card>
  );
}

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

      <CorpusBlock />

      <h2 className="mb-2 text-sm font-medium text-muted-foreground">Needs attention</h2>
      <SessionList unfinished pageSize={5} showEntity showArchived={false} />
    </div>
  );
}
