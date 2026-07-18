import { useUrlParam } from "../hooks/useUrlState";
import { InboxBadge } from "../components/StatusBadge";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
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
import { PageHeader } from "@/components/app/PageHeader";
import { EmptyState, ErrorNotice, LoadingState } from "@/components/app/states";
import { api, type EntityRef, type MergeCandidate } from "../api";
import { keys } from "../lib/keys";
import { FetchStatus } from "../components/FetchStatus";
import {
  SelectAllHeader,
  SelectionBar,
  useSelection,
} from "@/components/app/selection";

const TYPES = [
  { key: "tag", label: "Tags" },
  { key: "correspondent", label: "Correspondents" },
  { key: "document_type", label: "Document types" },
] as const;

type TypeKey = (typeof TYPES)[number]["key"];

function CandidateRow({ c, onReview }: { c: MergeCandidate; onReview: () => void }) {
  return (
    <li className="flex items-center gap-3 rounded-lg border border-amber-300/60 bg-amber-50 p-2 text-sm dark:border-amber-800 dark:bg-amber-950/40">
      <span className="flex-1">
        <strong>{c.source.name}</strong>{" "}
        <span className="text-muted-foreground">
          ({c.source.document_count ?? 0} docs)
        </span>
        {" → "}
        <strong>{c.target.name}</strong>{" "}
        <span className="text-muted-foreground">
          ({c.target.document_count ?? 0} docs)
        </span>
      </span>
      <span className="text-xs text-muted-foreground">
        {Math.round(Math.max(c.string_score, c.semantic_score ?? 0) * 100)}% similar
      </span>
      <Button size="sm" variant="secondary" onClick={onReview}>
        Review with agent
      </Button>
    </li>
  );
}

export default function Taxonomy() {
  const [typeRaw, setType] = useUrlParam("type", "tag");
  const type = (["tag", "correspondent", "document_type"] as const).includes(
    typeRaw as TypeKey,
  )
    ? (typeRaw as TypeKey)
    : "tag";
  const [filter, setFilter] = useUrlParam("name");
  const navigate = useNavigate();
  const selection = useSelection();

  const { data: entities, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: keys.entities(type),
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
    queryKey: keys.mergeCandidates(type),
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
    // One server-side job (progress, cancellation, retries) — never a
    // client-side POST loop with undefined partial-failure semantics.
    mutationFn: () =>
      api.createJob({ entity_type: type, entity_ids: [...selection.selected] }),
    onSuccess: (job) => {
      selection.clear();
      navigate(`/jobs/${job.id}`);
    },
  });

  const visible = (entities ?? []).filter(
    (e) => !filter || e.name.toLowerCase().includes(filter.toLowerCase()),
  );
  // The inbox tag is a workflow marker — never analyzable.
  const selectable = visible.filter((e) => !e.is_inbox_tag).map((e) => e.id);

  const switchType = (t: TypeKey) => {
    setType(t);
    selection.clear();
  };

  return (
    <div>
      <PageHeader
        title="Taxonomy"
        filters={
          <>
            {TYPES.map((t) => (
              <Button
                key={t.key}
                size="sm"
                variant={type === t.key ? "default" : "secondary"}
                onClick={() => switchType(t.key)}
              >
                {t.label}
              </Button>
            ))}
            <Input
              aria-label="filter entities"
              className="h-8 w-48"
              placeholder="filter by name…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </>
        }
      />

      <div className="mb-3">
        <FetchStatus resource={resource} isFetching={isFetching} onRefresh={() => refetch()} />
      </div>

      <SelectionBar
        selection={selection}
        allIds={selectable}
        actionLabel={`Analyze ${selection.selected.size} ${type.replaceAll("_", " ")}(s)`}
        busy={bulkAnalyze.isPending}
        onAction={() => bulkAnalyze.mutate()}
      />
      <ErrorNotice error={bulkAnalyze.error} />

      {candidates && candidates.length > 0 && (
        <div className="mb-6">
          <h2 className="mb-2 text-sm font-medium text-amber-700 dark:text-amber-400">
            Possible duplicates ({candidates.length})
          </h2>
          <ul className="space-y-2">
            {candidates.map((c, i) => (
              <CandidateRow key={i} c={c} onReview={() => reviewCandidate.mutate(c)} />
            ))}
          </ul>
        </div>
      )}

      {error && <ErrorNotice error={error} />}
      {isLoading ? (
        <LoadingState lines={5} />
      ) : visible.length === 0 ? (
        <EmptyState title={`No ${type.replaceAll("_", " ")}s match.`} />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8">
                <SelectAllHeader ids={selectable} selection={selection} />
              </TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Documents</TableHead>
              <TableHead>Matching rule</TableHead>
              <TableHead>Instructions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((e: EntityRef) => (
              <TableRow
                key={e.id}
                data-state={selection.selected.has(e.id) ? "selected" : undefined}
              >
                <TableCell>
                  <Checkbox
                    aria-label={`select ${e.name}`}
                    checked={selection.selected.has(e.id)}
                    disabled={e.is_inbox_tag}
                    title={e.is_inbox_tag ? "The inbox tag cannot be analyzed" : undefined}
                    onCheckedChange={() => selection.toggle(e.id)}
                  />
                </TableCell>
                <TableCell>
                  <Link
                    className="font-medium hover:text-primary hover:underline"
                    to={`/taxonomy/${type}/${e.id}`}
                  >
                    {e.name}
                  </Link>
                  {e.is_inbox_tag && (
                    <span className="ml-2 inline-flex">
                      <InboxBadge />
                    </span>
                  )}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {e.document_count ?? 0}
                </TableCell>
                <TableCell className="text-muted-foreground/70">
                  {e.match ? `${e.match}` : "—"}
                </TableCell>
                <TableCell className="max-w-64 truncate text-xs text-muted-foreground/70">
                  {e.instructions || "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <div className="mt-2">
        <ErrorNotice error={reviewCandidate.error} />
      </div>
    </div>
  );
}
