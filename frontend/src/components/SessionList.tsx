import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorNotice, LoadingState } from "@/components/app/states";
import { api, type Session } from "../api";
import { keys } from "../lib/keys";
import { Pager } from "./Pager";

// Session badge color follows the STATUS; its text shows the PHASE.
const statusColors: Record<string, string> = {
  idle: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  running: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  failed: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
};

function SessionRow({ s, showEntity }: { s: Session; showEntity: boolean }) {
  const qc = useQueryClient();
  const archive = useMutation({
    mutationFn: () =>
      s.archived_at ? api.unarchiveSession(s.id) : api.archiveSession(s.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.sessions() }),
  });
  const phaseLabel =
    s.phase && s.phase !== "done"
      ? s.phase.replaceAll("_", " ")
      : s.status === "idle"
        ? "finished"
        : s.status;
  return (
    <Card
      className={`p-3 transition-colors ${
        s.archived_at ? "opacity-60" : "hover:border-primary/50"
      }`}
    >
      <div className="flex w-full items-center gap-3">
        <Link className="flex min-w-0 flex-1 items-center gap-3" to={`/sessions/${s.id}`}>
          <span className="truncate text-sm font-medium">{s.title}</span>
          {showEntity && s.entity_type && (
            <Badge variant="secondary" className="shrink-0 font-normal text-muted-foreground">
              {s.entity_type.replaceAll("_", " ")}
            </Badge>
          )}
        </Link>
        <span className="flex shrink-0 items-center gap-2">
          {s.phase === "ocr_review" && !s.archived_at && (
            <Badge className="bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300">
              OCR review needed
            </Badge>
          )}
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
          <Badge
            variant="secondary"
            className={`capitalize ${statusColors[s.status] ?? "bg-muted"}`}
          >
            {phaseLabel}
          </Badge>
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
        </span>
      </div>
      {s.error && !s.archived_at && (
        <p className="mt-1 rounded-md bg-destructive/10 p-2 font-mono text-xs text-destructive">
          {s.error}
        </p>
      )}
    </Card>
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
      <ul className="space-y-2">
        {data.results.map((s) => (
          <li key={s.id}>
            <SessionRow s={s} showEntity={showEntity} />
          </li>
        ))}
      </ul>
      <Pager page={page} pageSize={pageSize} count={data.count} onPage={setPage} />
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
