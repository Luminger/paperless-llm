import { useEffect, useState } from "react";
import { useUrlNumber, useUrlParam, useUrlPatch } from "../hooks/useUrlState";
import { entityName, useEntityList } from "../hooks/useTaxonomy";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SimpleSelect } from "@/components/app/SimpleSelect";
import { PageHeader } from "@/components/app/PageHeader";
import { Pager } from "@/components/app/Pager";
import {
  SelectAllHeader,
  SelectionBar,
  useSelection,
} from "@/components/app/selection";
import { EmptyState, ErrorNotice, LoadingState } from "@/components/app/states";
import { api, type EntityRef } from "../api";
import { keys } from "../lib/keys";
import { formatDate } from "../lib/format";
import { FetchStatus } from "../components/FetchStatus";

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
  // Filters and page live in the URL: deep-linkable, refresh-proof.
  const [submitted] = useUrlParam("q");
  const [tagIdRaw] = useUrlNumber("tag");
  const [correspondentIdRaw] = useUrlNumber("correspondent");
  const [docTypeIdRaw] = useUrlNumber("type");
  const [page, setPage] = useUrlNumber("page", 1);
  const tagId = tagIdRaw || undefined;
  const correspondentId = correspondentIdRaw || undefined;
  const docTypeId = docTypeIdRaw || undefined;
  const [query, setQuery] = useState(submitted);
  const patchUrl = useUrlPatch();
  const navigate = useNavigate();
  const selection = useSelection();

  // Realtime search: debounced into the URL — no Search button.
  useEffect(() => {
    if (query === submitted) return;
    const t = setTimeout(() => patchUrl({ q: query, page: null }), 350);
    return () => clearTimeout(t);
  }, [query, submitted, patchUrl]);

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
    mutationFn: () => api.createJob({ document_ids: [...selection.selected] }),
    onSuccess: (job) => {
      selection.clear();
      navigate(`/jobs/${job.id}`);
    },
  });

  // `all` carries every matching id across pages (from paperless).
  const allIds = data?.all ?? data?.results.map((d) => d.id) ?? [];
  const pageIds = data?.results.map((d) => d.id) ?? [];

  return (
    <div>
      <PageHeader
        title="Documents"
        filters={
          <>
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Full-text search…"
              aria-label="full-text search"
              className="h-8 w-56"
            />
            <FilterSelect
              label="tag"
              value={tagId}
              options={tags}
              onChange={(v) => patchUrl({ tag: v, page: null })}
            />
            <FilterSelect
              label="correspondent"
              value={correspondentId}
              options={correspondents}
              onChange={(v) => patchUrl({ correspondent: v, page: null })}
            />
            <FilterSelect
              label="document type"
              value={docTypeId}
              options={docTypes}
              onChange={(v) => patchUrl({ type: v, page: null })}
            />
          </>
        }
      />

      <div className="mb-2">
        <FetchStatus resource="documents" isFetching={isFetching} onRefresh={() => refetch()} />
      </div>

      <SelectionBar
        selection={selection}
        allIds={allIds}
        actionLabel={`Analyze ${selection.selected.size} document(s) as job`}
        busy={bulkAnalyze.isPending}
        onAction={() => bulkAnalyze.mutate()}
      />
      <ErrorNotice error={bulkAnalyze.error} />

      {error && <ErrorNotice error={error} />}
      {isLoading ? (
        <LoadingState lines={5} />
      ) : data && data.results.length === 0 ? (
        <EmptyState
          title="No documents match."
          hint="Adjust the search or filters above."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8">
                <SelectAllHeader ids={pageIds} selection={selection} />
              </TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Correspondent</TableHead>
              <TableHead>Type</TableHead>
              <TableHead className="text-right">Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data?.results.map((d) => (
              <TableRow key={d.id} data-state={selection.selected.has(d.id) ? "selected" : undefined}>
                <TableCell>
                  <Checkbox
                    aria-label={`select ${d.title}`}
                    checked={selection.selected.has(d.id)}
                    onCheckedChange={() => selection.toggle(d.id)}
                  />
                </TableCell>
                <TableCell>
                  <Link
                    className="font-medium hover:text-primary hover:underline"
                    to={`/documents/${d.id}`}
                  >
                    {d.title || "(untitled)"}
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {entityName(correspondents, d.correspondent) || "—"}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {entityName(docTypes, d.document_type) || "—"}
                </TableCell>
                <TableCell className="text-right text-xs text-muted-foreground">
                  {formatDate(d.created)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <Pager
        page={page}
        pageSize={data?.page_size ?? 25}
        count={data?.count ?? 0}
        onPage={setPage}
        label="documents"
      />
    </div>
  );
}
