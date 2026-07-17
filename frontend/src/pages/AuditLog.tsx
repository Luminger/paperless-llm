import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type AuditEntry } from "../api";
import { Pager } from "../components/Pager";

const KIND_COLORS: Record<string, string> = {
  proposal: "bg-emerald-100 text-emerald-800",
  campaign: "bg-purple-100 text-purple-800",
  webhook: "bg-blue-100 text-blue-800",
  session: "bg-zinc-100 text-zinc-600",
  system: "bg-amber-100 text-amber-800",
};

function describe(e: AuditEntry): React.ReactNode {
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
    case "campaign/created":
      return (
        <>
          campaign #{String(d.job_id)} created for {(d.documents as number[])?.length ?? 0} document(s)
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
    case "system/started":
      return <>application started</>;
    default:
      return (
        <>
          {e.kind} {e.action} {JSON.stringify(d).slice(0, 120)}
        </>
      );
  }
}

export default function AuditLog() {
  const [page, setPage] = useState(1);
  const pageSize = 20;
  const { data, error } = useQuery({
    queryKey: ["audit", page],
    queryFn: () => api.listAudit(page, pageSize),
    refetchInterval: 10000,
  });

  if (error) return <p className="text-red-600">{String(error)}</p>;

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold">Log</h1>
      <p className="mb-4 text-sm text-zinc-500">
        Audit trail of everything the application did: applied and reverted changes,
        campaigns, webhook ingests, archive operations, application starts.
      </p>
      <ul className="divide-y divide-zinc-100 rounded border border-zinc-200 bg-white">
        {(data?.results ?? []).map((e) => (
          <li key={e.id} className="flex items-center gap-3 p-2.5 text-sm">
            <span className="w-40 shrink-0 font-mono text-xs text-zinc-400">
              {new Date(e.ts).toLocaleString()}
            </span>
            <span
              className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${KIND_COLORS[e.kind] ?? "bg-zinc-100"}`}
            >
              {e.kind}
            </span>
            <span className="min-w-0 text-zinc-700">{describe(e)}</span>
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
