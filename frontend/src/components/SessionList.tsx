import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Session } from "../api";
import { Pager } from "./Pager";

const statusColors: Record<string, string> = {
  idle: "bg-emerald-100 text-emerald-800",
  running: "bg-blue-100 text-blue-800",
  failed: "bg-red-100 text-red-700",
};

function SessionRow({ s, showEntity }: { s: Session; showEntity: boolean }) {
  const qc = useQueryClient();
  const archive = useMutation({
    mutationFn: () =>
      s.archived_at ? api.unarchiveSession(s.id) : api.archiveSession(s.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });
  return (
    <li
      className={`rounded border bg-white p-3 ${
        s.archived_at ? "border-zinc-100 opacity-70" : "border-zinc-200 hover:border-emerald-400"
      }`}
    >
      <div className="flex w-full items-center gap-3">
        <Link className="flex min-w-0 flex-1 items-center gap-3" to={`/sessions/${s.id}`}>
          <span className="font-mono text-sm text-zinc-400">#{s.id}</span>
          {showEntity && s.entity_type && (
            <span className="font-medium">
              {s.entity_type.replaceAll("_", " ")} {s.entity_id}
            </span>
          )}
          <span className="truncate text-sm text-zinc-500">{s.title}</span>
        </Link>
        <span className="flex shrink-0 items-center gap-2">
          {s.phase === "ocr_review" && !s.archived_at && (
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
            {s.phase && s.phase !== "done"
              ? s.phase.replaceAll("_", " ")
              : s.status === "idle"
                ? "finished"
                : s.status}
          </span>
          <button
            className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-500 hover:bg-zinc-200"
            title={
              s.archived_at
                ? "Unarchive (allows applying again)"
                : "Archive (keeps history & revert, blocks applying)"
            }
            onClick={() => archive.mutate()}
          >
            {s.archived_at ? "Unarchive" : "Archive"}
          </button>
        </span>
      </div>
      {s.error && !s.archived_at && (
        <p className="mt-1 rounded bg-red-50 p-2 font-mono text-xs text-red-700">{s.error}</p>
      )}
    </li>
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
  const { data, isLoading, error } = useQuery({
    queryKey: ["sessions", { entityType, entityId, archived, unfinished, page, pageSize }],
    queryFn: () =>
      api.listSessions({
        entity_type: entityType,
        entity_id: entityId,
        archived,
        unfinished,
        page,
        page_size: pageSize,
      }),
    refetchInterval: archived ? false : 5000,
  });
  if (isLoading) return <p className="text-sm text-zinc-500">Loading…</p>;
  if (error) return <p className="text-sm text-red-600">{String(error)}</p>;
  if (!data || data.count === 0)
    return <p className="text-sm text-zinc-400">{emptyText}</p>;
  return (
    <div className="space-y-2">
      <ul className="space-y-2">
        {data.results.map((s) => (
          <SessionRow key={s.id} s={s} showEntity={showEntity} />
        ))}
      </ul>
      <Pager page={page} pageSize={pageSize} count={data.count} onPage={setPage} />
    </div>
  );
}

/** Generic session list: active sessions paginated, archived sessions in
 * a collapsed section with their own pagination. Used by the Analyses
 * page and by every entity detail page. */
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
        className="rounded border border-zinc-100 p-2"
      >
        <summary className="cursor-pointer text-xs text-zinc-400 select-none">
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
