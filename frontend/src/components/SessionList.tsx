import { useState } from "react";
import { CircleStop } from "lucide-react";
import { Tip } from "@/components/app/Tip";
import { OcrReviewBadge, SessionStatusBadge } from "./StatusBadge";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState, ErrorNotice, LoadingState } from "@/components/app/states";
import { Checkbox } from "@/components/ui/checkbox";
import { SelectAllHeader, useSelection } from "@/components/app/selection";
import { api, type Session } from "../api";
import { formatDateTime } from "../lib/format";
import { errorMessage } from "../lib/errors";
import { keys } from "../lib/keys";
import { Pager } from "@/components/app/Pager";
import { FramedCard } from "@/components/app/Framed";

function SessionRow({
  s,
  showEntity,
  selection,
}: {
  s: Session;
  showEntity: boolean;
  selection?: ReturnType<typeof useSelection>;
}) {
  const qc = useQueryClient();
  const archive = useMutation({
    mutationFn: () =>
      s.archived_at ? api.unarchiveSession(s.id) : api.archiveSession(s.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.sessions() }),
  });
  const stop = useMutation({
    mutationFn: () => api.cancelSession(s.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.sessions() }),
  });
  const busy = !s.archived_at && (s.status === "running" || s.phase === "queued");
  return (
    <>
      <TableRow className={s.archived_at ? "opacity-60" : ""}>
        {selection && (
          <TableCell>
            <Checkbox
              aria-label={`select session ${s.id}`}
              checked={selection.selected.has(s.id)}
              onCheckedChange={() => selection.toggle(s.id)}
            />
          </TableCell>
        )}
        <TableCell className="max-w-0">
          <Link
            className="truncate font-medium hover:text-primary hover:underline"
            to={`/sessions/${s.id}`}
          >
            {/* Entity names resolve live server-side; the run title is
                the session's own name. */}
            {showEntity && s.entity_name ? s.entity_name : s.title}
          </Link>
          {showEntity && s.entity_name && (
            <span className="ml-2 text-xs text-muted-foreground">{s.title}</span>
          )}
          {showEntity && !s.entity_name && s.entity_type && (
            <Badge
              variant="secondary"
              className="ml-2 shrink-0 font-normal text-muted-foreground"
            >
              {s.entity_type.replaceAll("_", " ")}
            </Badge>
          )}
        </TableCell>
        <TableCell className="whitespace-nowrap text-muted-foreground">
          {formatDateTime(s.created_at)}
        </TableCell>
        <TableCell>
          <span className="flex items-center gap-2">
            {s.phase === "ocr_review" && !s.archived_at && <OcrReviewBadge />}
            {s.pending_proposal_count > 0 ? (
              <Badge variant="secondary" className="text-primary">
                {s.pending_proposal_count === 1
                  ? "proposal to review"
                  : `${s.pending_proposal_count} proposals to review`}
              </Badge>
            ) : s.applied_proposal_count > 0 ? (
              // Say what HAPPENED, not "decided" (reads like declined).
              <Badge variant="secondary" className="font-normal text-muted-foreground">
                {s.applied_proposal_count} applied
              </Badge>
            ) : s.proposal_count > 0 ? (
              <Badge variant="secondary" className="font-normal text-muted-foreground">
                nothing applied
              </Badge>
            ) : (
              s.phase === "done" && (
                <Badge variant="secondary" className="font-normal text-muted-foreground">
                  no changes proposed
                </Badge>
              )
            )}
          </span>
        </TableCell>
        <TableCell>
          <SessionStatusBadge status={s.status} phase={s.phase} error={s.error} />
        </TableCell>
        <TableCell className="text-right">
          {busy && (
            <Tip
              content={
                stop.isError
                  ? errorMessage(stop.error)
                  : "Stop this run — aborts the model call, cancels queued work. Retry revives it."
              }
            >
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-xs text-muted-foreground"
                disabled={stop.isPending}
                onClick={() => stop.mutate()}
                aria-invalid={stop.isError || undefined}
              >
                <CircleStop className="size-3.5" />
                {stop.isError ? "Failed — retry" : "Stop"}
              </Button>
            </Tip>
          )}
          <Tip
            content={
              archive.isError
                ? errorMessage(archive.error)
                : s.archived_at
                  ? "Unarchive (allows applying again)"
                  : "Archive (keeps history & revert, blocks applying)"
            }
          >
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs text-muted-foreground"
              onClick={() => archive.mutate()}
              aria-invalid={archive.isError || undefined}
            >
              {archive.isError ? "Failed — retry" : s.archived_at ? "Unarchive" : "Archive"}
            </Button>
          </Tip>
        </TableCell>
      </TableRow>

    </>
  );
}

/** THE session table markup — dashboard, entity pages, job details. */
export function SessionTable({
  sessions,
  showEntity,
  selection,
}: {
  sessions: Session[];
  showEntity: boolean;
  /** Opt-in checkbox column (the job page's bulk actions). */
  selection?: ReturnType<typeof useSelection>;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {selection && (
            <TableHead className="w-10">
              <SelectAllHeader
                ids={sessions.map((s) => s.id)}
                selection={selection}
                label="select all sessions on this page"
              />
            </TableHead>
          )}
          <TableHead>Session</TableHead>
          <TableHead className="w-40">Started</TableHead>
          <TableHead className="w-44">Attention</TableHead>
          <TableHead className="w-32">Status</TableHead>
          <TableHead className="w-24 text-right" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {sessions.map((s) => (
          <SessionRow key={s.id} s={s} showEntity={showEntity} selection={selection} />
        ))}
      </TableBody>
    </Table>
  );
}

function PagedList({
  entityType,
  entityId,
  archived,
  unfinished,
  pageSize,
  showEntity,
  emptyText,
}: {
  entityType?: string;
  entityId?: number;
  archived: boolean;
  unfinished?: boolean;
  pageSize: number;
  showEntity: boolean;
  emptyText: string;
}) {
  const [page, setPage] = useState(1);
  const filter = {
    entity_type: entityType,
    entity_id: entityId,
    archived,
    unfinished,
    page,
    page_size: pageSize,
  };
  const { data, isLoading, error } = useQuery({
    queryKey: keys.sessions(filter),
    queryFn: () => api.listSessions(filter),
    refetchInterval: archived ? false : 5000,
  });
  if (isLoading) return <LoadingState lines={2} />;
  if (error) return <ErrorNotice error={error} />;
  if (!data || data.count === 0) return <EmptyState title={emptyText} />;
  return (
    <div className="space-y-2">
      <SessionTable sessions={data.results} showEntity={showEntity} />
      <Pager
        page={page}
        pageSize={pageSize}
        count={data.count}
        onPage={setPage}
        label="sessions"
      />
    </div>
  );
}

/** Generic session list: active sessions paginated, archived sessions in
 * a collapsed section with their own pagination. Used by the dashboard
 * and by every entity detail page. */
export function SessionList({
  entityType,
  entityId,
  pageSize = 5,
  showEntity = true,
  unfinished = false,
  showArchived: showArchivedSection = true,
  framed = false,
}: {
  entityType?: string;
  entityId?: number;
  pageSize?: number;
  showEntity?: boolean;
  unfinished?: boolean;
  showArchived?: boolean;
  /** Detail pages: sessions and archived sessions as trace-style boxes. */
  framed?: boolean;
}) {
  const [showArchived, setShowArchived] = useState(false);
  const active = (
    <PagedList
      entityType={entityType}
      entityId={entityId}
      archived={false}
      unfinished={unfinished}
      pageSize={pageSize}
      showEntity={showEntity}
      emptyText={unfinished ? "Nothing needs attention." : "No sessions yet."}
    />
  );
  const archivedList = (
    <PagedList
      entityType={entityType}
      entityId={entityId}
      archived={true}
      pageSize={pageSize}
      showEntity={showEntity}
      emptyText="No archived sessions."
    />
  );
  if (framed) {
    // Detail pages: each list is a box of its own, in the trace frame.
    return (
      <div className="space-y-4">
        <FramedCard title="Sessions">{active}</FramedCard>
        {showArchivedSection && (
          <FramedCard title="Archived sessions" defaultOpen={false}>
            {archivedList}
          </FramedCard>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {active}
      {showArchivedSection && (
        <details
          onToggle={(e) => setShowArchived((e.target as HTMLDetailsElement).open)}
          className="rounded-lg border border-dashed p-2"
        >
          <summary className="cursor-pointer text-xs text-muted-foreground select-none">
            Archived sessions
          </summary>
          <div className="mt-2">{showArchived && archivedList}</div>
        </details>
      )}
    </div>
  );
}
