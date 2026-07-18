import { useUrlNumber, useUrlParam, useUrlPatch } from "../hooks/useUrlState";
import { entityName, useEntityList } from "../hooks/useTaxonomy";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { MultiFilter } from "@/components/app/MultiFilter";
import { PageHeader } from "@/components/app/PageHeader";
import { ResetFilters } from "@/components/app/ResetFilters";
import { UrlSearchInput } from "@/components/app/UrlSearchInput";
import { useClampPage } from "../hooks/useListPage";
import { Pager } from "@/components/app/Pager";
import {
  SelectAllHeader,
  SelectionBar,
  useSelection,
} from "@/components/app/selection";
import { EmptyState, ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../api";
import { keys } from "../lib/keys";
import { formatDate } from "../lib/format";
import { FetchStatus } from "../components/FetchStatus";

/** "1,5,9" from the URL -> [1, 5, 9]; empty -> []. */
function ids(v: string): number[] {
  return v ? v.split(",").map(Number).filter(Boolean) : [];
}

export default function Documents() {
  // Filters and page live in the URL: deep-linkable, refresh-proof.
  const [submitted] = useUrlParam("q");
  const [tagsRaw] = useUrlParam("tags");
  const [correspondentsRaw] = useUrlParam("correspondents");
  const [typesRaw] = useUrlParam("types");
  const [page, setPage] = useUrlNumber("page", 1);
  const [pageSize] = useUrlNumber("size", 25);
  const tagIds = ids(tagsRaw);
  const correspondentIds = ids(correspondentsRaw);
  const typeIds = ids(typesRaw);
  const patchUrl = useUrlPatch();
  const navigate = useNavigate();
  const selection = useSelection();

  const { data: tags } = useEntityList("tag");
  const { data: correspondents } = useEntityList("correspondent");
  const { data: docTypes } = useEntityList("document_type");

  const filters = {
    query: submitted || undefined,
    tag_ids: tagIds,
    correspondent_ids: correspondentIds,
    document_type_ids: typeIds,
  };
  const filtersActive =
    Boolean(submitted) ||
    tagIds.length + correspondentIds.length + typeIds.length > 0;
  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: keys.documents({ ...filters, page_size: pageSize }, page),
    queryFn: () => api.listDocuments({ ...filters, page, page_size: pageSize }),
  });
  useClampPage(page, setPage, data, pageSize, error);

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
            <UrlSearchInput
              placeholder="Full-text search…"
              ariaLabel="full-text search"
              className="h-8 w-56"
            />
            <MultiFilter
              label="tag"
              options={tags}
              values={tagIds}
              onChange={(v) => patchUrl({ tags: v.join(",") || null, page: null })}
            />
            <MultiFilter
              label="correspondent"
              options={correspondents}
              values={correspondentIds}
              onChange={(v) =>
                patchUrl({ correspondents: v.join(",") || null, page: null })
              }
            />
            <MultiFilter
              label="document type"
              plural="document types"
              options={docTypes}
              values={typeIds}
              onChange={(v) => patchUrl({ types: v.join(",") || null, page: null })}
            />
            <ResetFilters
              active={filtersActive}
              onReset={() => {
                patchUrl({
                  q: null,
                  tags: null,
                  correspondents: null,
                  types: null,
                  page: null,
                });
              }}
            />
          </>
        }
      />

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
        pageSize={pageSize}
        count={data?.count ?? 0}
        onPage={setPage}
        onPageSize={(n) => patchUrl({ size: n === 25 ? null : n, page: null })}
        label="documents"
        status={
          <FetchStatus
            resource="documents"
            isFetching={isFetching}
            onRefresh={() => refetch()}
          />
        }
      />
    </div>
  );
}
