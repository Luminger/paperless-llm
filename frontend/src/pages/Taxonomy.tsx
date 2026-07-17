import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type EntityRef, type MergeCandidate } from "../api";
import { FetchStatus } from "../components/FetchStatus";
import { MultiSelectBar, useMultiSelect } from "../components/MultiSelect";
import { errorMessage } from "../lib/errors";

const TYPES = [
  { key: "tag", label: "Tags" },
  { key: "correspondent", label: "Correspondents" },
  { key: "document_type", label: "Document types" },
] as const;

type TypeKey = (typeof TYPES)[number]["key"];

function CandidateRow({ c, onReview }: { c: MergeCandidate; onReview: () => void }) {
  return (
    <li className="flex items-center gap-3 rounded border border-amber-200 bg-amber-50 p-2 text-sm">
      <span className="flex-1">
        <strong>{c.source.name}</strong>{" "}
        <span className="text-zinc-500">({c.source.document_count ?? 0} docs)</span>
        {" → "}
        <strong>{c.target.name}</strong>{" "}
        <span className="text-zinc-500">({c.target.document_count ?? 0} docs)</span>
      </span>
      <span className="text-xs text-zinc-500">
        {Math.round(Math.max(c.string_score, c.semantic_score ?? 0) * 100)}% similar
      </span>
      <button
        className="rounded bg-amber-600 px-2 py-1 text-xs text-white hover:bg-amber-700"
        onClick={onReview}
      >
        Review with agent
      </button>
    </li>
  );
}

export default function Taxonomy() {
  const [type, setType] = useState<TypeKey>("tag");
  const [filter, setFilter] = useState("");
  const navigate = useNavigate();
  const ms = useMultiSelect();

  const { data: entities, isFetching, refetch } = useQuery({
    queryKey: ["taxonomy", type],
    queryFn: () =>
      type === "tag"
        ? api.listTags()
        : type === "correspondent"
          ? api.listCorrespondents()
          : api.listDocumentTypes(),
  });
  const resource =
    type === "tag" ? "tags" : type === "correspondent" ? "correspondents" : "document_types";
  const { data: candidates } = useQuery({
    queryKey: ["merge-candidates", type],
    queryFn: () => api.mergeCandidates(type),
  });

  const reviewCandidate = useMutation({
    mutationFn: (c: MergeCandidate) =>
      api.analyzeEntity(
        type,
        c.source.id,
        `This ${type.replaceAll("_", " ")} may be a duplicate of "${c.target.name}" (id=${c.target.id}). Verify and merge if appropriate.`,
      ),
    onSuccess: (s) => navigate(`/sessions/${s.id}`),
  });

  const bulkAnalyze = useMutation({
    mutationFn: async () => {
      for (const id of ms.selected) {
        await api.analyzeEntity(type, id);
      }
    },
    onSuccess: () => {
      ms.cancel();
      navigate("/");
    },
  });

  const visible = (entities ?? []).filter(
    (e) => !filter || e.name.toLowerCase().includes(filter.toLowerCase()),
  );
  // The inbox tag is a workflow marker — never analyzable.
  const selectable = visible.filter((e) => !e.is_inbox_tag).map((e) => e.id);

  const switchType = (t: TypeKey) => {
    setType(t);
    ms.cancel();
  };

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Taxonomy</h1>
      <div className="mb-3 flex items-center gap-2">
        {TYPES.map((t) => (
          <button
            key={t.key}
            onClick={() => switchType(t.key)}
            className={`rounded px-3 py-1.5 text-sm ${
              type === t.key ? "bg-zinc-800 text-white" : "bg-zinc-100 text-zinc-600"
            }`}
          >
            {t.label}
          </button>
        ))}
        <input
          aria-label="filter entities"
          className="ml-2 rounded border border-zinc-200 px-2 py-1 text-sm"
          placeholder="filter by name…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <span className="flex-1" />
        {!ms.active && (
          <button
            className="rounded bg-zinc-100 px-2.5 py-1 text-xs text-zinc-600 hover:bg-zinc-200"
            onClick={() => ms.setActive(true)}
          >
            Select…
          </button>
        )}
      </div>

      <div className="mb-3">
        <FetchStatus resource={resource} isFetching={isFetching} onRefresh={() => refetch()} />
      </div>

      {ms.active && (
        <div className="mb-3">
          <MultiSelectBar
            count={ms.selected.size}
            allIds={selectable}
            actionLabel={`Analyze ${ms.selected.size} ${type.replaceAll("_", " ")}(s)`}
            busy={bulkAnalyze.isPending}
            onAction={() => bulkAnalyze.mutate()}
            onSelectAll={ms.selectAll}
            onUnselectAll={ms.unselectAll}
            onCancel={ms.cancel}
          />
          {bulkAnalyze.error && (
            <p className="mt-1 text-xs text-red-600">{errorMessage(bulkAnalyze.error)}</p>
          )}
        </div>
      )}

      {candidates && candidates.length > 0 && (
        <div className="mb-6">
          <h2 className="mb-2 text-sm font-medium text-amber-700">
            Possible duplicates ({candidates.length})
          </h2>
          <ul className="space-y-2">
            {candidates.map((c, i) => (
              <CandidateRow key={i} c={c} onReview={() => reviewCandidate.mutate(c)} />
            ))}
          </ul>
        </div>
      )}

      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-zinc-300 text-left text-zinc-500">
            {ms.active && <th className="w-8 py-2" />}
            <th className="py-2 pr-4 font-medium">Name</th>
            <th className="py-2 pr-4 font-medium">Documents</th>
            <th className="py-2 pr-4 font-medium">Matching rule</th>
            <th className="py-2 pr-4 font-medium">Instructions</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((e: EntityRef) => (
            <tr key={e.id} className="border-b border-zinc-100 hover:bg-zinc-50">
              {ms.active && (
                <td className="py-2">
                  <input
                    type="checkbox"
                    aria-label={`select ${e.name}`}
                    checked={ms.selected.has(e.id)}
                    disabled={e.is_inbox_tag}
                    title={e.is_inbox_tag ? "The inbox tag cannot be analyzed" : undefined}
                    onChange={() => ms.toggle(e.id)}
                  />
                </td>
              )}
              <td className="py-2 pr-4">
                <Link
                  className="hover:text-emerald-700 hover:underline"
                  to={`/taxonomy/${type}/${e.id}`}
                >
                  {e.name}
                </Link>
                {e.is_inbox_tag && (
                  <span className="ml-2 rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-700">
                    inbox
                  </span>
                )}
              </td>
              <td className="py-2 pr-4 text-zinc-500">{e.document_count ?? 0}</td>
              <td className="py-2 pr-4 text-zinc-400">{e.match ? `${e.match}` : "—"}</td>
              <td className="max-w-64 truncate py-2 pr-4 text-xs text-zinc-400">
                {e.instructions || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {reviewCandidate.error && (
        <p className="mt-2 text-sm text-red-600">{errorMessage(reviewCandidate.error)}</p>
      )}
    </div>
  );
}
