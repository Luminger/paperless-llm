import { useState } from "react";
import { useEntityList } from "../hooks/useTaxonomy";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { SimpleSelect } from "@/components/app/SimpleSelect";
import { PageHeader } from "@/components/app/PageHeader";
import { EmptyState, ErrorNotice, LoadingState } from "@/components/app/states";
import { api, type EntityRef } from "../api";
import { keys } from "../lib/keys";
import { formatDate } from "../lib/format";
import { FetchStatus } from "../components/FetchStatus";
import { MultiSelectBar, useMultiSelect } from "../components/MultiSelect";
import { Pager } from "../components/Pager";

const ANY = "__any__";

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
    <SimpleSelect
      ariaLabel={`filter by ${label}`}
      value={value != null ? String(value) : ANY}
      onValueChange={(v) => onChange(v === ANY ? undefined : Number(v))}
      options={[
        { value: ANY, label: `any ${label}` },
        ...(options ?? []).map((o) => ({ value: String(o.id), label: o.name })),
      ]}
    />
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

  const { data: tags } = useEntityList("tag");
  const { data: correspondents } = useEntityList("correspondent");
  const { data: docTypes } = useEntityList("document_type");

  const filters = {
    query: submitted || undefined,
    tag_id: tagId,
    correspondent_id: correspondentId,
    document_type_id: docTypeId,
  };
  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: keys.documents(filters, page),
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
      <PageHeader
        title="Documents"
        actions={
          !ms.active && (
            <Button variant="secondary" size="sm" onClick={() => ms.setActive(true)}>
              Select…
            </Button>
          )
        }
        filters={
          <>
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                setSubmitted(query);
                setPage(1);
              }}
            >
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Full-text search…"
                className="h-8 w-56"
              />
              <Button type="submit" size="sm" variant="secondary">
                Search
              </Button>
            </form>
            <FilterSelect
              label="tag"
              value={tagId}
              options={tags}
              onChange={(v) => {
                setTagId(v);
                setPage(1);
              }}
            />
            <FilterSelect
              label="correspondent"
              value={correspondentId}
              options={correspondents}
              onChange={(v) => {
                setCorrespondentId(v);
                setPage(1);
              }}
            />
            <FilterSelect
              label="document type"
              value={docTypeId}
              options={docTypes}
              onChange={(v) => {
                setDocTypeId(v);
                setPage(1);
              }}
            />
          </>
        }
      />

      <div className="mb-2">
        <FetchStatus resource="documents" isFetching={isFetching} onRefresh={() => refetch()} />
      </div>

      {ms.active && (
        <div className="mb-3 space-y-1">
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
          <ErrorNotice error={bulkAnalyze.error} />
        </div>
      )}

      {error && <ErrorNotice error={error} />}
      {isLoading ? (
        <LoadingState lines={5} />
      ) : data && data.results.length === 0 ? (
        <EmptyState
          title="No documents match."
          hint="Adjust the search or filters above."
        />
      ) : (
        <ul className="divide-y rounded-lg border bg-card">
          {data?.results.map((d) => (
            <li key={d.id} className="flex items-center gap-3 p-3">
              {ms.active && (
                <Checkbox
                  aria-label={`select document ${d.id}`}
                  checked={ms.selected.has(d.id)}
                  onCheckedChange={() => ms.toggle(d.id)}
                />
              )}
              <Link
                className="font-medium hover:text-primary hover:underline"
                to={`/documents/${d.id}`}
              >
                {d.title || "(untitled)"}
              </Link>
              <span className="text-xs text-muted-foreground">{formatDate(d.created)}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2 flex items-center justify-between">
        <Pager page={page} pageSize={25} count={data?.count ?? 0} onPage={setPage} />
        {data && (
          <p className="text-xs text-muted-foreground">{data.count} documents</p>
        )}
      </div>
    </div>
  );
}
