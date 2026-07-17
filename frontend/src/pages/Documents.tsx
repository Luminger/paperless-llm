import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { FetchStatus } from "../components/FetchStatus";

export default function Documents() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [dialogDoc, setDialogDoc] = useState<number | null>(null);
  const [redoOcr, setRedoOcr] = useState(
    () => localStorage.getItem("pllm.redoOcr") === "1",
  );
  const [instructions, setInstructions] = useState("");
  const navigate = useNavigate();

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ["documents", submitted],
    queryFn: () => api.listDocuments(submitted || undefined),
  });

  const analyze = useMutation({
    mutationFn: (docId: number) =>
      api.analyzeDocument(docId, {
        redo_ocr: redoOcr,
        instructions: instructions || undefined,
      }),
    onSuccess: (session) => navigate(`/sessions/${session.id}`),
  });

  const startAnalysis = () => {
    localStorage.setItem("pllm.redoOcr", redoOcr ? "1" : "0");
    if (dialogDoc !== null) analyze.mutate(dialogDoc);
  };

  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <h1 className="text-xl font-semibold">Documents</h1>
        <form
          className="ml-auto flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setSubmitted(query);
          }}
        >
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Full-text search…"
            className="w-64 rounded border border-zinc-300 px-2 py-1 text-sm"
          />
          <button className="rounded bg-zinc-900 px-3 py-1 text-sm text-white">
            Search
          </button>
        </form>
      </div>

      {analyze.error && (
        <p className="mb-3 rounded bg-red-50 p-2 text-sm text-red-700">
          {String(analyze.error)}
        </p>
      )}

      {isLoading && <p className="text-zinc-500">Loading…</p>}
      <div className="mb-2">
        <FetchStatus resource="documents" isFetching={isFetching} onRefresh={() => refetch()} />
      </div>
      <ul className="divide-y divide-zinc-100 rounded border border-zinc-200 bg-white">
        {data?.results.map((d) => (
          <li key={d.id}>
            <div className="flex items-center gap-3 p-3">
              <span className="font-mono text-xs text-zinc-400">#{d.id}</span>
              <Link
                className="font-medium hover:text-emerald-700 hover:underline"
                to={`/documents/${d.id}`}
              >
                {d.title || "(untitled)"}
              </Link>
              <span className="text-xs text-zinc-400">{d.created ?? ""}</span>
              <button
                onClick={() => setDialogDoc(dialogDoc === d.id ? null : d.id)}
                className="ml-auto rounded bg-emerald-600 px-2.5 py-1 text-xs text-white hover:bg-emerald-700"
              >
                Analyze
              </button>
            </div>
            {dialogDoc === d.id && (
              <div className="space-y-2 border-t border-zinc-100 bg-zinc-50 p-3">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={redoOcr}
                    onChange={(e) => setRedoOcr(e.target.checked)}
                  />
                  Re-do OCR first (you review the result before the analysis runs)
                </label>
                <input
                  className="w-full rounded border border-zinc-200 px-2 py-1 text-sm"
                  placeholder="Optional instructions for the agent…"
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                />
                <button
                  onClick={startAnalysis}
                  disabled={analyze.isPending}
                  className="rounded bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  Start analysis
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {data && (
        <p className="mt-2 text-xs text-zinc-400">{data.count} documents</p>
      )}
    </div>
  );
}
