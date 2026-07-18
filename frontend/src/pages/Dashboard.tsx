import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { FramedCard } from "@/components/app/Framed";
import { Progress } from "@/components/ui/progress";
import { PageHeader } from "@/components/app/PageHeader";
import { SimpleSelect } from "@/components/app/SimpleSelect";
import { ErrorNotice } from "@/components/app/states";
import { Link } from "react-router-dom";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "../api";
import { keys } from "../lib/keys";
import { formatDate } from "../lib/format";
import { entityName, useEntityList } from "../hooks/useTaxonomy";
import { SessionList } from "../components/SessionList";

/** The inbox backlog: documents waiting to be looked at (fresh
 * arrivals without an active session). The list IS the work — one
 * button sends the whole inbox through analysis. */
function InboxBlock() {
  const navigate = useNavigate();
  const { data } = useQuery({
    queryKey: keys.inbox(),
    queryFn: api.getInbox,
    refetchInterval: 30_000,
  });
  const { data: correspondents } = useEntityList("correspondent");
  const start = useMutation({
    mutationFn: () => api.createJob({ inbox: true }),
    onSuccess: (job) => navigate(`/jobs/${job.id}`),
  });
  if (!data || data.count === 0) return null;
  return (
    <FramedCard
      className="mb-4"
      title="Inbox"
      meta={`${data.count} document${data.count === 1 ? "" : "s"} waiting`}
      footer={
        <>
          <ErrorNotice error={start.error} />
          <span className="flex-1" />
          <Button size="sm" disabled={start.isPending} onClick={() => start.mutate()}>
            Analyze the inbox
          </Button>
        </>
      }
    >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead className="w-56">Correspondent</TableHead>
              <TableHead className="w-32 text-right">Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.results.map((d) => (
              <TableRow key={d.id}>
                <TableCell className="max-w-0">
                  <Link
                    className="truncate font-medium hover:text-primary hover:underline"
                    to={`/documents/${d.id}`}
                  >
                    {d.title || "(untitled)"}
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {entityName(correspondents, d.correspondent) || "—"}
                </TableCell>
                <TableCell className="text-right text-muted-foreground">
                  {d.created ? formatDate(d.created) : "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {data.count > data.results.length && (
          <p className="mt-2 text-xs text-muted-foreground">
            …and {data.count - data.results.length} more.
          </p>
        )}
    </FramedCard>
  );
}

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
    <FramedCard
      className="mb-4"
      title="Corpus"
      meta={`${data.processed.toLocaleString()} of ${data.total.toLocaleString()} analyzed`}
      footer={
        done ? (
          <p className="text-sm text-muted-foreground">
            Every document has been analyzed.
          </p>
        ) : (
          <>
            <ErrorNotice error={start.error} />
            <span className="flex-1" />
            <SimpleSelect
              ariaLabel="batch size"
              value={size}
              onValueChange={setSize}
              options={["10", "25", "50"].map((n) => ({
                value: n,
                label: `${n} documents`,
              }))}
            />
            <Button size="sm" disabled={start.isPending} onClick={() => start.mutate()}>
              Analyze next batch
            </Button>
          </>
        )
      }
    >
      <div className="flex items-center gap-3">
        <Progress value={pct} className="max-w-96 flex-1" />
        <p className="text-xs whitespace-nowrap text-muted-foreground">
          {pct}%
        </p>
      </div>
    </FramedCard>
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

      <InboxBlock />
      <CorpusBlock />

      <FramedCard title="Needs attention">
        <SessionList unfinished pageSize={5} showEntity showArchived={false} />
      </FramedCard>
    </div>
  );
}
