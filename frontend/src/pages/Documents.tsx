import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api, type EntityRef } from "../api";
import { FetchStatus } from "../components/FetchStatus";
import { MultiSelectBar, useMultiSelect } from "../components/MultiSelect";
import { Pager } from "../components/Pager";

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: number | undefined;
  options: EntityRef[] | undefined;
  onChange: (v: number | undefined) => void;
}) {
  return (
    <select
      aria-label={`filter by ${label}`}
      className="rounded border border-zinc-200 px-2 py-1 text-sm text-zinc-600"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
    >
      <option value="">any {label}</option>
      {(options ?? []).map((o) => (
        <option key={o.id} value={o.id}>
          {o.name}
        </option>
      ))}
    </select>
  );
}

export default function Documents() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [tagId, setTagId] = useState<number | undefined>();
  const [correspondentId, setCorrespondentId] = useState<number | undefined>();
  const [docTypeId, setDocTypeId] = useState<number | undefined>();
  const [page, setPage] = useState(1);
  const navigate = useNavigate();
  const ms = useMultiSelect();

  const { data: tags } = useQuery({ queryKey: ["tags"], queryFn: api.listTags });
  const { data: correspondents } = useQuery({
    queryKey: ["correspondents"],
    queryFn: api.listCorrespondents,
  });
  const { data: docTypes } = useQuery({
    queryKey: ["document_types"],
    queryFn: api.listDocumentTypes,
  });

  const filters = {
    query: submitted || undefined,
    tag_id: tagId,
    correspondent_id: correspondentId,
    document_type_id: docTypeId,
  };
  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ["documents", filters, page],
    queryFn: () => api.listDocuments({ ...filters, page }),
  });

  const bulkAnalyze = useMutation({
    mutationFn: () => api.createJob({ document_ids: [...ms.selected] }),
    onSuccess: (job) => {
      ms.cancel();
      navigate(`/jobs/${job.id}`);
    },
  });

  // `all` carries every matching id across pages (from paperless).
  const allIds = data?.all ?? data?.results.map((d) => d.id) ?? [];

  return (
    <div>
      <div className="mb-3 flex items-center gap-3">
        <h1 className="text-xl font-semibold">Documents</h1>
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

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setSubmitted(query);
            setPage(1);
          }}
        >
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Full-text search…"
            className="w-56 rounded border border-zinc-300 px-2 py-1 text-sm"
          />
          <button className="rounded bg-zinc-900 px-3 py-1 text-sm text-white">
            Search
          </button>
        </form>
        <FilterSelect label="tag" value={tagId} options={tags} onChange={(v) => { setTagId(v); setPage(1); }} />
        <FilterSelect
          label="correspondent"
          value={correspondentId}
          options={correspondents}
          onChange={(v) => { setCorrespondentId(v); setPage(1); }}
        />
        <FilterSelect
          label="document type"
          value={docTypeId}
          options={docTypes}
          onChange={(v) => { setDocTypeId(v); setPage(1); }}
        />
      </div>

      <div className="mb-2">
        <FetchStatus resource="documents" isFetching={isFetching} onRefresh={() => refetch()} />
      </div>

      {ms.active && (
        <div className="mb-3">
          <MultiSelectBar
            count={ms.selected.size}
            allIds={allIds}
            actionLabel={`Analyze ${ms.selected.size} document(s) as job`}
            busy={bulkAnalyze.isPending}
            onAction={() => bulkAnalyze.mutate()}
            onSelectAll={ms.selectAll}
            onUnselectAll={ms.unselectAll}
            onCancel={ms.cancel}
          />
          {bulkAnalyze.error && (
            <p className="mt-1 text-xs text-red-600">{String(bulkAnalyze.error)}</p>
          )}
        </div>
      )}

      {isLoading && <p className="text-zinc-500">Loading…</p>}
      <ul className="divide-y divide-zinc-100 rounded border border-zinc-200 bg-white">
        {data?.results.map((d) => (
          <li key={d.id} className="flex items-center gap-3 p-3">
            {ms.active && (
              <input
                type="checkbox"
                aria-label={`select document ${d.id}`}
                checked={ms.selected.has(d.id)}
                onChange={() => ms.toggle(d.id)}
              />
            )}
            <span className="font-mono text-xs text-zinc-400">#{d.id}</span>
            <Link
              className="font-medium hover:text-emerald-700 hover:underline"
              to={`/documents/${d.id}`}
            >
              {d.title || "(untitled)"}
            </Link>
            <span className="text-xs text-zinc-400">{d.created ?? ""}</span>
          </li>
        ))}
      </ul>
      <div className="mt-2 flex items-center justify-between">
        <Pager page={page} pageSize={25} count={data?.count ?? 0} onPage={setPage} />
        {data && <p className="text-xs text-zinc-400">{data.count} documents</p>}
      </div>
    </div>
  );
}
