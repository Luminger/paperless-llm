import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type AuditEntry } from "../api";
import { Pager } from "../components/Pager";

const KIND_COLORS: Record<string, string> = {
  proposal: "bg-emerald-100 text-emerald-800",
  job: "bg-purple-100 text-purple-800",
  webhook: "bg-blue-100 text-blue-800",
  session: "bg-zinc-100 text-zinc-600",
  paperless: "bg-sky-100 text-sky-700",
};

function ActorBadge({ actor }: { actor: string }) {
  const isUser = actor.startsWith("user");
  return (
    <span
      className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
        isUser ? "bg-amber-100 text-amber-800" : "bg-zinc-100 text-zinc-500"
      }`}
      title={isUser ? "Triggered by a user action" : "Triggered by the application"}
    >
      {actor}
    </span>
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
              <Link className="text-emerald-700 hover:underline" to={`/sessions/${d.session_id}`}>
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
          <Link className="text-emerald-700 hover:underline" to={`/sessions/${d.session_id}`}>
            session #{String(d.session_id)}
          </Link>{" "}
          {e.action}
        </>
      );
    case "paperless/fetch":
    case "paperless/write":
      return (
        <span className={e.action === "write" ? "font-medium" : "text-zinc-500"}>
          {String(d.method)} {String(d.path)}
          {d.status != null && <span className="text-zinc-400"> → {String(d.status)}</span>}
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
  const keys = Object.keys(diff);
  if (keys.length === 0) return null;
  return (
    <table className="w-full table-fixed text-xs">
      <thead>
        <tr className="border-b border-zinc-200 text-left text-zinc-400">
          <th className="w-40 py-1 pr-2 font-medium">Field</th>
          <th className="w-1/2 py-1 pr-2 font-medium">From</th>
          <th className="py-1 font-medium">To</th>
        </tr>
      </thead>
      <tbody>
        {keys.map((k) => (
          <tr key={k} className="border-b border-zinc-100 align-top">
            <td className="py-1 pr-2 font-mono text-zinc-500">{k}</td>
            <td className="py-1 pr-2 break-words whitespace-pre-wrap text-red-700/80">
              {JSON.stringify(diff[k].from) ?? "—"}
            </td>
            <td className="py-1 break-words whitespace-pre-wrap text-emerald-700/90">
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
    <div className="space-y-2 border-t border-zinc-100 bg-zinc-50/60 p-3">
      {diff && Object.keys(diff).length > 0 && <DiffTable diff={diff} />}
      {Object.keys(rest).length > 0 && (
        <pre className="font-mono text-[11px] break-words whitespace-pre-wrap text-zinc-500">
          {JSON.stringify(rest, null, 1)}
        </pre>
      )}
    </div>
  );
}

const FILTERS = [
  { key: "", label: "Everything" },
  { key: "changes", label: "Data changes" },
  { key: "paperless", label: "Paperless traffic" },
] as const;

export default function AuditLog() {
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<string>("");
  const pageSize = 20;
  const { data, error } = useQuery({
    queryKey: ["audit", filter, page],
    queryFn: () => api.listAudit(page, pageSize, filter || undefined),
    refetchInterval: 10000,
  });

  if (error) return <p className="text-red-600">{String(error)}</p>;

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">Log</h1>
      <p className="mb-3 text-sm text-zinc-500">
        Audit trail: every read and write against paperless, every change the
        application made (with its from → to diff), attributed to who caused it.
      </p>
      <div className="mb-3 flex gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => {
              setFilter(f.key);
              setPage(1);
            }}
            className={`rounded px-2.5 py-1 text-xs ${
              filter === f.key ? "bg-zinc-800 text-white" : "bg-zinc-100 text-zinc-600"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
      <ul className="divide-y divide-zinc-100 rounded border border-zinc-200 bg-white">
        {(data?.results ?? []).map((e) => (
          <li key={e.id}>
            <details>
              <summary className="flex cursor-pointer items-center gap-3 p-2.5 text-sm select-none hover:bg-zinc-50">
                <span className="w-40 shrink-0 font-mono text-xs text-zinc-400">
                  {new Date(e.ts).toLocaleString()}
                </span>
                <span
                  className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${KIND_COLORS[e.kind] ?? "bg-zinc-100"}`}
                >
                  {e.kind}
                </span>
                <ActorBadge actor={e.actor} />
                <span className="min-w-0 flex-1 truncate text-zinc-700">{summary(e)}</span>
              </summary>
              <EntryDetails e={e} />
            </details>
          </li>
        ))}
        {data && data.count === 0 && (
          <li className="p-4 text-sm text-zinc-400">Nothing logged yet.</li>
        )}
      </ul>
      <div className="mt-2">
        <Pager page={page} pageSize={pageSize} count={data?.count ?? 0} onPage={setPage} />
      </div>
    </div>
  );
}
