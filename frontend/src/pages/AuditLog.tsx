import { useState } from "react";
import { useUrlNumber, useUrlParam, useUrlPatch } from "../hooks/useUrlState";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader } from "@/components/app/PageHeader";
import { Pager } from "@/components/app/Pager";
import { SimpleSelect } from "@/components/app/SimpleSelect";
import { EmptyState, ErrorNotice } from "@/components/app/states";
import { api, type AuditEntry } from "../api";
import { keys } from "../lib/keys";
import { formatDateTime } from "../lib/format";

const KIND_COLORS: Record<string, string> = {
  proposal:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  job: "bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300",
  webhook: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  session: "bg-muted text-muted-foreground",
  paperless: "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300",
  task: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
};

function ActorBadge({ actor }: { actor: string }) {
  const isUser = actor.startsWith("user");
  return (
    <Badge
      variant="secondary"
      className={
        isUser
          ? "shrink-0 bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
          : "shrink-0 text-muted-foreground"
      }
      title={isUser ? "Triggered by a user action" : "Triggered by the application"}
    >
      {actor}
    </Badge>
  );
}

function summary(e: AuditEntry): React.ReactNode {
  const d = e.detail;
  switch (`${e.kind}/${e.action}`) {
    case "proposal/applied":
    case "proposal/reverted":
    case "proposal/no_change":
      return (
        <>
          proposal #{String(d.proposal_id)} ({String(d.proposal_kind ?? "?").replaceAll("_", " ")}){" "}
          {e.action === "no_change" ? "needed no change" : e.action}
          {d.session_id != null && (
            <>
              {" — "}
              <Link className="text-primary hover:underline" to={`/sessions/${d.session_id}`}>
                session #{String(d.session_id)}
              </Link>
            </>
          )}
        </>
      );
    case "job/created":
      return (
        <>
          job #{String(d.job_id)} created for {(d.documents as number[])?.length ?? 0} document(s)
          {d.apply_policy === "auto" ? " · auto-apply" : ""}
        </>
      );
    case "webhook/ingested":
      return <>webhook queued document(s) {(d.documents as number[])?.join(", ")}</>;
    case "session/archived":
    case "session/unarchived":
      return (
        <>
          <Link className="text-primary hover:underline" to={`/sessions/${d.session_id}`}>
            session #{String(d.session_id)}
          </Link>{" "}
          {e.action}
        </>
      );
    case "task/scheduled":
    case "task/retry_requested":
    case "task/redone":
      return (
        <>
          {e.action === "scheduled" && "queued"}
          {e.action === "retry_requested" && "manual retry of"}
          {e.action === "redone" && "redid"}{" "}
          {String(d.step_kind)} step{" — "}
          <Link className="text-primary hover:underline" to={`/sessions/${d.session_id}`}>
            session #{String(d.session_id)}
          </Link>
        </>
      );
    case "task/retry_scheduled":
      return (
        <>
          automatic retry {String(d.attempt)} of {String(d.step_kind)} step scheduled{" — "}
          <Link className="text-primary hover:underline" to={`/sessions/${d.session_id}`}>
            session #{String(d.session_id)}
          </Link>
        </>
      );
    case "paperless/fetch":
    case "paperless/write":
      return (
        <span className={e.action === "write" ? "font-medium" : "text-muted-foreground"}>
          {String(d.method)} {String(d.path)}
          {d.status != null && (
            <span className="text-muted-foreground/60"> → {String(d.status)}</span>
          )}
        </span>
      );
    default:
      return (
        <>
          {e.kind} {e.action}
        </>
      );
  }
}

function DiffTable({ diff }: { diff: Record<string, { from: unknown; to: unknown }> }) {
  const fields = Object.keys(diff);
  if (fields.length === 0) return null;
  return (
    <table className="w-full table-fixed text-xs">
      <thead>
        <tr className="border-b text-left text-muted-foreground/60">
          <th className="w-40 py-1 pr-2 font-medium">Field</th>
          <th className="w-1/2 py-1 pr-2 font-medium">From</th>
          <th className="py-1 font-medium">To</th>
        </tr>
      </thead>
      <tbody>
        {fields.map((k) => (
          <tr key={k} className="border-b border-border/50 align-top">
            <td className="py-1 pr-2 font-mono text-muted-foreground">{k}</td>
            <td className="py-1 pr-2 break-words whitespace-pre-wrap text-red-700/80 dark:text-red-400/80">
              {JSON.stringify(diff[k].from) ?? "—"}
            </td>
            <td className="py-1 break-words whitespace-pre-wrap text-emerald-700/90 dark:text-emerald-400/90">
              {JSON.stringify(diff[k].to) ?? "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function EntryDetails({ e }: { e: AuditEntry }) {
  const diff = e.detail.diff as Record<string, { from: unknown; to: unknown }> | undefined;
  const rest = Object.fromEntries(
    Object.entries(e.detail).filter(([k]) => k !== "diff"),
  );
  return (
    <div className="space-y-2 border-t bg-muted/40 p-3">
      {diff && Object.keys(diff).length > 0 && <DiffTable diff={diff} />}
      {Object.keys(rest).length > 0 && (
        <pre className="font-mono text-[11px] break-words whitespace-pre-wrap text-muted-foreground">
          {JSON.stringify(rest, null, 1)}
        </pre>
      )}
    </div>
  );
}

const ALL = "__all__";

const FILTERS = [
  { value: ALL, label: "everything" },
  { value: "changes", label: "data changes" },
  { value: "task", label: "tasks" },
  { value: "paperless", label: "paperless traffic" },
];

function LogRow({ e }: { e: AuditEntry }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <TableRow
        className="cursor-pointer"
        data-state={open ? "selected" : undefined}
        onClick={(ev) => {
          // Links inside the summary navigate; they don't toggle.
          if ((ev.target as HTMLElement).closest("a")) return;
          setOpen(!open);
        }}
      >
        <TableCell className="font-mono text-xs whitespace-nowrap text-muted-foreground/70">
          {formatDateTime(e.ts)}
        </TableCell>
        <TableCell>
          <Badge
            variant="secondary"
            className={`shrink-0 ${KIND_COLORS[e.kind] ?? "bg-muted"}`}
          >
            {e.kind}
          </Badge>
        </TableCell>
        <TableCell>
          <ActorBadge actor={e.actor} />
        </TableCell>
        <TableCell className="max-w-0 truncate">{summary(e)}</TableCell>
      </TableRow>
      {open && (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={4} className="p-0">
            <EntryDetails e={e} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

export default function AuditLog() {
  const [page, setPage] = useUrlNumber("page", 1);
  const [filter] = useUrlParam("kind");
  const patchUrl = useUrlPatch();
  // Filter changes reset the page in the SAME URL update.
  const setFilter = (v: string) => patchUrl({ kind: v, page: null });
  const pageSize = 20;
  const { data, error } = useQuery({
    queryKey: keys.audit(filter, page),
    queryFn: () => api.listAudit(page, pageSize, filter || undefined),
    refetchInterval: 10000,
  });

  return (
    <div>
      <PageHeader
        title="Log"
        filters={
          <SimpleSelect
            ariaLabel="filter by log kind"
            value={filter || ALL}
            onValueChange={(v) => setFilter(v === ALL ? "" : v)}
            options={FILTERS}
          />
        }
      />
      <p className="-mt-2 mb-3 text-sm text-muted-foreground">
        Audit trail: every read and write against paperless, every change the
        application made (with its from → to diff), attributed to who caused it.
      </p>
      <ErrorNotice error={error} />
      {data && data.count === 0 ? (
        <EmptyState title="Nothing logged yet." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-44">Time</TableHead>
              <TableHead className="w-28">Kind</TableHead>
              <TableHead className="w-24">Actor</TableHead>
              <TableHead>Event</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(data?.results ?? []).map((e) => (
              <LogRow key={e.id} e={e} />
            ))}
          </TableBody>
        </Table>
      )}
      <Pager
        page={page}
        pageSize={pageSize}
        count={data?.count ?? 0}
        onPage={setPage}
        label="entries"
      />
    </div>
  );
}
