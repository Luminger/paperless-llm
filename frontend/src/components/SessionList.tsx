import { useState } from "react";
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
import { api, type Session } from "../api";
import { keys } from "../lib/keys";
import { Pager } from "@/components/app/Pager";

function SessionRow({ s, showEntity }: { s: Session; showEntity: boolean }) {
  const qc = useQueryClient();
  const archive = useMutation({
    mutationFn: () =>
      s.archived_at ? api.unarchiveSession(s.id) : api.archiveSession(s.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.sessions() }),
  });
  return (
    <>
      <TableRow className={s.archived_at ? "opacity-60" : ""}>
        <TableCell className="max-w-0">
          <Link
            className="truncate font-medium hover:text-primary hover:underline"
            to={`/sessions/${s.id}`}
          >
            {s.title}
          </Link>
          {showEntity && s.entity_type && (
            <Badge
              variant="secondary"
              className="ml-2 shrink-0 font-normal text-muted-foreground"
            >
              {s.entity_type.replaceAll("_", " ")}
            </Badge>
          )}
        </TableCell>
        <TableCell>
          <span className="flex items-center gap-2">
            {s.phase === "ocr_review" && !s.archived_at && <OcrReviewBadge />}
            {s.proposal_count > 0 ? (
              <Badge variant="secondary" className="text-primary">
                {s.proposal_count} proposal{s.proposal_count > 1 ? "s" : ""}
              </Badge>
            ) : (
              s.phase === "done" && (
                <Badge variant="secondary" className="text-muted-foreground">
                  no changes proposed
                </Badge>
              )
            )}
          </span>
        </TableCell>
        <TableCell>
          <SessionStatusBadge status={s.status} phase={s.phase} />
        </TableCell>
        <TableCell className="text-right">
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs text-muted-foreground"
            title={
              s.archived_at
                ? "Unarchive (allows applying again)"
                : "Archive (keeps history & revert, blocks applying)"
            }
            onClick={() => archive.mutate()}
          >
            {s.archived_at ? "Unarchive" : "Archive"}
          </Button>
        </TableCell>
      </TableRow>
      {s.error && !s.archived_at && (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={4} className="py-1">
            <p className="rounded-md bg-destructive/10 p-2 font-mono text-xs text-destructive">
              {s.error}
            </p>
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

/** THE session table markup — dashboard, entity pages, job details. */
export function SessionTable({
  sessions,
  showEntity,
}: {
  sessions: Session[];
  showEntity: boolean;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Session</TableHead>
          <TableHead className="w-44">Attention</TableHead>
          <TableHead className="w-32">Status</TableHead>
          <TableHead className="w-24 text-right" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {sessions.map((s) => (
          <SessionRow key={s.id} s={s} showEntity={showEntity} />
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
}: {
  entityType?: string;
  entityId?: number;
  pageSize?: number;
  showEntity?: boolean;
  unfinished?: boolean;
  showArchived?: boolean;
}) {
  const [showArchived, setShowArchived] = useState(false);
  return (
    <div className="space-y-3">
      <PagedList
        entityType={entityType}
        entityId={entityId}
        archived={false}
        unfinished={unfinished}
        pageSize={pageSize}
        showEntity={showEntity}
        emptyText={unfinished ? "Nothing needs attention." : "No sessions yet."}
      />
      {showArchivedSection && (
        <details
          onToggle={(e) => setShowArchived((e.target as HTMLDetailsElement).open)}
          className="rounded-lg border border-dashed p-2"
        >
          <summary className="cursor-pointer text-xs text-muted-foreground select-none">
            Archived sessions
          </summary>
          <div className="mt-2">
            {showArchived && (
              <PagedList
                entityType={entityType}
                entityId={entityId}
                archived={true}
                pageSize={pageSize}
                showEntity={showEntity}
                emptyText="No archived sessions."
              />
            )}
          </div>
        </details>
      )}
    </div>
  );
}
