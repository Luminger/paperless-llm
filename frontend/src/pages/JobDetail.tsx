// One job: its controls (pause/resume, bulk retry, cancel) and its
// sessions as THE list pattern — paginated, status-multifiltered,
// checkbox-multiselect with a bulk-retry toolbar. Session rows show
// the DOCUMENT (entity name resolves live server-side); the run title
// alone would read "Analysis" on every row.
import { Link, useParams } from "react-router-dom";
import { SessionTable } from "../components/SessionList";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CirclePause, CirclePlay, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/app/Tip";
import { CancelJobDialog } from "@/components/app/CancelJobDialog";
import { MultiFilter } from "@/components/app/MultiFilter";
import { PageHeader } from "@/components/app/PageHeader";
import { Pager } from "@/components/app/Pager";
import { SelectionBar, useSelection } from "@/components/app/selection";
import { EmptyState, ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../api";
import { keys } from "../lib/keys";
import { errorMessage } from "../lib/errors";
import { useListPage, useClampPage } from "../hooks/useListPage";
import { useUrlParam } from "../hooks/useUrlState";
import { StatusBadge } from "../components/StatusBadge";
import { scopeLabel } from "../lib/labels";

const STATUS_OPTIONS = [
  { id: "idle", name: "finished / stopped" },
  { id: "running", name: "running" },
  { id: "failed", name: "failed" },
];

export default function JobDetail() {
  const { id } = useParams();
  const jobId = Number(id);
  const qc = useQueryClient();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const { page, setPage, pageSize } = useListPage(25);
  const [statusRaw, setStatusRaw] = useUrlParam("status");
  const statuses = statusRaw ? statusRaw.split(",") : [];
  const selection = useSelection(`job-${jobId}`);

  const { data: job, error } = useQuery({
    queryKey: keys.job(jobId),
    queryFn: () => api.getJob(jobId),
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return s === "queued" || s === "running" ? 2000 : false;
    },
  });
  const active = job?.status === "queued" || job?.status === "running";
  const sessionsQuery = useQuery({
    queryKey: keys.sessions({ job_id: jobId, status: statuses, page, page_size: pageSize }),
    queryFn: () =>
      api.listSessions({
        job_id: jobId,
        status: statuses.length ? statuses : undefined,
        page,
        page_size: pageSize,
      }),
    refetchInterval: active ? 2000 : false,
  });
  useClampPage(page, setPage, sessionsQuery.data, pageSize);

  // Flow-through review: one button that opens the first session
  // waiting on the user; each session's flow bar carries on from there.
  const { data: attention } = useQuery({
    queryKey: keys.jobAttention(jobId),
    queryFn: () => api.getJobAttention(jobId),
    // Poll only while there is anything to wait for (AUDIT FP-L6) — a
    // finished job left open in a tab must not hit the API forever.
    refetchInterval: (q) => {
      const remaining = (q.state.data?.remaining ?? 0) > 0;
      return active || remaining ? 5000 : false;
    },
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: keys.job(jobId) });
    qc.invalidateQueries({ queryKey: keys.sessions() });
  };
  const pauseToggle = useMutation({
    mutationFn: () =>
      job?.status === "paused" ? api.resumeJob(jobId) : api.pauseJob(jobId),
    onSuccess: refresh,
  });
  const retrySelected = useMutation({
    mutationFn: (ids?: number[]) => api.retryJob(jobId, ids),
    onSuccess: () => {
      selection.clear();
      refresh();
    },
  });

  if (error) return <ErrorNotice error={error} />;
  if (!job) return <LoadingState lines={4} />;

  const cancellable = active || job.status === "paused";
  const retryable = job.failed > 0 || job.stopped > 0;
  const sessions = sessionsQuery.data?.results ?? [];

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
            {retryable && (
              <Tip content="Run every failed or stopped session of this job again">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={retrySelected.isPending}
                  onClick={() => retrySelected.mutate(undefined)}
                >
                  <RotateCcw className="size-3.5" />
                  Retry {job.failed + job.stopped} failed
                </Button>
              </Tip>
            )}
            {(active || job.status === "paused") && (
              <Tip
                content={
                  pauseToggle.isError
                    ? errorMessage(pauseToggle.error)
                    : job.status === "paused"
                      ? "Resume: workers pick this job's work up again"
                      : "Pause: running steps finish, nothing new starts until resumed"
                }
              >
                <Button
                  variant="outline"
                  size="sm"
                  disabled={pauseToggle.isPending}
                  onClick={() => pauseToggle.mutate()}
                  aria-invalid={pauseToggle.isError || undefined}
                >
                  {job.status === "paused" ? (
                    <>
                      <CirclePlay className="size-3.5" /> Continue
                    </>
                  ) : (
                    <>
                      <CirclePause className="size-3.5" /> Pause
                    </>
                  )}
                </Button>
              </Tip>
            )}
            {cancellable && (
              <Button variant="secondary" size="sm" onClick={() => setConfirmCancel(true)}>
                Cancel job
              </Button>
            )}
            <StatusBadge status={job.status} />
          </div>
        }
      />
      <CancelJobDialog
        job={job}
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        onDone={refresh}
      />
      <div className="-mt-2 mb-4 flex items-center gap-4">
        <p className="text-sm text-muted-foreground">
          {job.done} ok
          {job.failed > 0 && `, ${job.failed} failed`}
          {job.stopped > 0 && `, ${job.stopped} stopped`}
          {" of "}
          {job.total}
        </p>
        <span className="flex-1" />
        <MultiFilter<string>
          label="status"
          plural="statuses"
          options={STATUS_OPTIONS}
          values={statuses}
          onChange={(v) => {
            setStatusRaw(v.length ? v.join(",") : "");
            setPage(1);
          }}
        />
      </div>
      <SelectionBar
        selection={selection}
        allIds={sessions.map((s) => s.id)}
        actionLabel={`Retry ${selection.selected.size} selected`}
        busy={retrySelected.isPending}
        onAction={() => retrySelected.mutate([...selection.selected])}
      />
      <ErrorNotice error={retrySelected.error} />
      {sessionsQuery.error ? (
        <ErrorNotice error={sessionsQuery.error} />
      ) : !sessionsQuery.data ? (
        <LoadingState lines={5} />
      ) : sessions.length === 0 ? (
        <EmptyState title="No sessions match these filters" />
      ) : (
        <>
          <SessionTable sessions={sessions} showEntity={true} selection={selection} />
          <Pager
            page={page}
            pageSize={pageSize}
            count={sessionsQuery.data.count}
            onPage={setPage}
          />
        </>
      )}
    </div>
  );
}
