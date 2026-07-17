import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type EntityRef, type MergeCandidate } from "../api";

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
  const navigate = useNavigate();

  const { data: entities } = useQuery({
    queryKey: ["taxonomy", type],
    queryFn: () =>
      type === "tag"
        ? api.listTags()
        : type === "correspondent"
          ? api.listCorrespondents()
          : api.listDocumentTypes(),
  });
  const { data: candidates } = useQuery({
    queryKey: ["merge-candidates", type],
    queryFn: () => api.mergeCandidates(type),
  });

  const analyze = useMutation({
    mutationFn: ({ id, instructions }: { id: number; instructions?: string }) =>
      api.analyzeEntity(type, id, instructions),
    onSuccess: (s) => navigate(`/sessions/${s.id}`),
  });

  const reviewCandidate = (c: MergeCandidate) =>
    analyze.mutate({
      id: c.source.id,
      instructions: `This ${type.replace("_", " ")} may be a duplicate of "${c.target.name}" (id=${c.target.id}). Verify and merge if appropriate.`,
    });

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Taxonomy</h1>
      <div className="mb-4 flex gap-2">
        {TYPES.map((t) => (
          <button
            key={t.key}
            onClick={() => setType(t.key)}
            className={`rounded px-3 py-1.5 text-sm ${
              type === t.key ? "bg-zinc-800 text-white" : "bg-zinc-100 text-zinc-600"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {candidates && candidates.length > 0 && (
        <div className="mb-6">
          <h2 className="mb-2 text-sm font-medium text-amber-700">
            Possible duplicates ({candidates.length})
          </h2>
          <ul className="space-y-2">
            {candidates.map((c, i) => (
              <CandidateRow key={i} c={c} onReview={() => reviewCandidate(c)} />
            ))}
          </ul>
        </div>
      )}

      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-zinc-300 text-left text-zinc-500">
            <th className="py-2 pr-4 font-medium">Name</th>
            <th className="py-2 pr-4 font-medium">Documents</th>
            <th className="py-2 pr-4 font-medium">Matching rule</th>
            <th className="py-2" />
          </tr>
        </thead>
        <tbody>
          {(entities ?? []).map((e: EntityRef) => (
            <tr key={e.id} className="border-b border-zinc-100 hover:bg-zinc-50">
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
              <td className="py-2 pr-4 text-zinc-400">
                {e.match ? `${e.match}` : "—"}
              </td>
              <td className="py-2 text-right">
                <button
                  className="rounded bg-emerald-600 px-2 py-1 text-xs text-white hover:bg-emerald-700 disabled:opacity-50"
                  disabled={analyze.isPending}
                  onClick={() => analyze.mutate({ id: e.id })}
                >
                  Analyze
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {analyze.error && <p className="mt-2 text-sm text-red-600">{String(analyze.error)}</p>}
    </div>
  );
}
