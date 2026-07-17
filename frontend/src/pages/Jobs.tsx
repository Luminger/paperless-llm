import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Job, type JobCreate } from "../api";
import { StatusBadge } from "../components/StatusBadge";
import { errorMessage } from "../lib/errors";

function Progress({ job }: { job: Job }) {
  const finished = job.done + job.failed;
  const pct = job.total ? Math.round((finished / job.total) * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-32 overflow-hidden rounded bg-zinc-200">
        <div
          className={`h-full ${job.failed ? "bg-amber-500" : "bg-emerald-500"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-zinc-500">
        {job.done} ok{job.failed ? `, ${job.failed} failed` : ""} / {job.total}
      </span>
    </div>
  );
}

function scopeLabel(job: Job): string {
  const p = job.params;
  if (p.inbox) return "Inbox";
  if (p.untagged_only) return "Untagged documents";
  if (p.tag_id) return `Tag #${p.tag_id}`;
  if (Array.isArray(p.document_ids)) return `${p.document_ids.length} selected documents`;
  return job.kind;
}

function NewJob({ onDone }: { onDone: () => void }) {
  const [scope, setScope] = useState<"inbox" | "tag" | "untagged">("inbox");
  const [tagId, setTagId] = useState<number | undefined>();
  const [redoOcr, setRedoOcr] = useState(false);
  const [auto, setAuto] = useState(false);
  const [instructions, setInstructions] = useState("");
  const { data: tags } = useQuery({ queryKey: ["tags"], queryFn: api.listTags });

  const create = useMutation({
    mutationFn: (body: JobCreate) => api.createJob(body),
    onSuccess: onDone,
  });

  return (
    <form
      className="mb-6 space-y-3 rounded border border-zinc-200 bg-white p-4"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate({
          inbox: scope === "inbox",
          untagged_only: scope === "untagged",
          tag_id: scope === "tag" ? tagId : undefined,
          redo_ocr: redoOcr,
          apply_policy: auto ? "auto" : "review",
          instructions: instructions.trim() || undefined,
        });
      }}
    >
      <p className="font-medium">New job</p>
      <div className="flex gap-4 text-sm">
        {(
          [
            ["inbox", "Inbox documents"],
            ["untagged", "Untagged documents"],
            ["tag", "Documents with tag"],
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="flex items-center gap-1.5">
            <input
              type="radio"
              name="scope"
              checked={scope === key}
              onChange={() => setScope(key)}
            />
            {label}
          </label>
        ))}
      </div>
      {scope === "tag" && (
        <select
          aria-label="job tag"
          className="w-full rounded border border-zinc-300 p-2 text-sm"
          value={tagId ?? ""}
          onChange={(e) => setTagId(e.target.value === "" ? undefined : Number(e.target.value))}
        >
          <option value="">pick a tag…</option>
          {(tags ?? [])
            .filter((t) => !t.is_inbox_tag)
            .map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
        </select>
      )}
      <div className="flex gap-4 text-sm">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={redoOcr} onChange={(e) => setRedoOcr(e.target.checked)} />
          re-do OCR (each document gates for review)
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          auto-apply proposals (journaled &amp; revertible)
        </label>
      </div>
      <textarea
        aria-label="job instructions"
        className="w-full rounded border border-zinc-300 p-2 text-sm"
        rows={2}
        placeholder="Optional instructions for the agent…"
        value={instructions}
        onChange={(e) => setInstructions(e.target.value)}
      />
      <button
        type="submit"
        className="rounded bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-700 disabled:opacity-50"
        disabled={create.isPending || (scope === "tag" && !tagId)}
      >
        Start job
      </button>
      {create.error && <p className="text-sm text-red-600">{errorMessage(create.error)}</p>}
    </form>
  );
}

export default function Jobs() {
  const qc = useQueryClient();
  const [showNew, setShowNew] = useState(false);
  const { data, error } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.listJobs(),
    refetchInterval: (q) =>
      (q.state.data?.results ?? []).some(
        (j) => j.status === "queued" || j.status === "running",
      )
        ? 2000
        : false,
  });
  const jobs = data?.results;
  const cancel = useMutation({
    mutationFn: (id: number) => api.cancelJob(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });

  if (error) return <p className="text-red-600">{errorMessage(error)}</p>;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Jobs</h1>
        <button
          className="rounded bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-700"
          onClick={() => setShowNew(!showNew)}
        >
          {showNew ? "Close" : "New job"}
        </button>
      </div>
      {showNew && (
        <NewJob
          onDone={() => {
            setShowNew(false);
            qc.invalidateQueries({ queryKey: ["jobs"] });
          }}
        />
      )}
      <ul className="space-y-2">
        {(jobs ?? []).map((j) => (
          <li
            key={j.id}
            className="flex items-center gap-4 rounded border border-zinc-200 bg-white p-3 text-sm"
          >
            <span className="w-12 text-zinc-400">#{j.id}</span>
            <span className="flex-1">
              {scopeLabel(j)}
              {j.params.apply_policy === "auto" && (
                <span className="ml-2 rounded bg-purple-100 px-1.5 py-0.5 text-xs text-purple-700">
                  auto-apply
                </span>
              )}
            </span>
            <Progress job={j} />
            <StatusBadge status={j.status} />
            {(j.status === "queued" || j.status === "running") && (
              <button
                className="rounded bg-zinc-200 px-2 py-1 text-xs hover:bg-zinc-300"
                onClick={() => cancel.mutate(j.id)}
              >
                Cancel
              </button>
            )}
            <Link className="text-xs text-emerald-700 hover:underline" to={`/jobs/${j.id}`}>
              details
            </Link>
          </li>
        ))}
        {jobs && jobs.length === 0 && (
          <p className="text-sm text-zinc-500">No jobs yet.</p>
        )}
      </ul>
    </div>
  );
}
