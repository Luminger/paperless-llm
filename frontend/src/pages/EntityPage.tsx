import { useState } from "react";
import { entityName, useTaxonomyLists } from "../hooks/useTaxonomy";
import { InboxBadge } from "../components/StatusBadge";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ExternalLink, ScanText, Sparkles, User } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { DocumentPreview } from "@/components/app/DocumentPreview";
import { EmptyState, ErrorNotice, LoadingState } from "@/components/app/states";
import { api, type DocumentHistory, type EntityRef, type PaperlessDocument } from "../api";
import { keys, invalidateEntities } from "../lib/keys";
import { formatDate, formatDateTime, matchingRule } from "../lib/format";
import { SessionList } from "../components/SessionList";
import { errorMessage } from "../lib/errors";

// ---------------------------------------------------------------------
// One generic entity overview page for documents AND taxonomy entities:
// facts about the entry (entity-valued fields link to their own detail
// pages), a link back to the entry in paperless, and the entity's
// session history (active + archived, paginated).
// ---------------------------------------------------------------------

export function entityHref(entityType: string, id: number): string {
  return entityType === "document" ? `/documents/${id}` : `/taxonomy/${entityType}/${id}`;
}

function paperlessHref(base: string, entityType: string, id: number): string {
  // Paperless has no per-entity detail routes; taxonomy links go to its
  // management pages (documents keep their real detail page).
  switch (entityType) {
    case "document":
      return `${base}/documents/${id}/details`;
    case "tag":
      return `${base}/tags`;
    case "correspondent":
      return `${base}/correspondents`;
    case "document_type":
      return `${base}/documenttypes`;
    case "storage_path":
      return `${base}/storagepaths`;
    default:
      return base;
  }
}

function FactRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[10rem_1fr] gap-3 border-b border-border/50 py-2 text-sm last:border-b-0">
      <div className="text-muted-foreground">{label}</div>
      <div>{children}</div>
    </div>
  );
}

function EntityLink({
  entityType,
  id,
  list,
}: {
  entityType: string;
  id: number | null;
  list: EntityRef[] | undefined;
}) {
  if (id == null) return <span className="text-muted-foreground/60">—</span>;
  const name = entityName(list, id);
  return (
    <Link className="text-primary hover:underline" to={entityHref(entityType, id)}>
      {name}
    </Link>
  );
}

function DocumentFacts({ doc }: { doc: PaperlessDocument }) {
  const { tags, correspondents, docTypes, storagePaths } = useTaxonomyLists();
  return (
    <div className="flex gap-6">
      <DocumentPreview
        documentId={doc.id}
        title={doc.title || "document"}
        className="h-48 w-36"
      />
      <div className="flex-1">
        <FactRow label="Title">{doc.title || "—"}</FactRow>
        <FactRow label="Correspondent">
          <EntityLink
            entityType="correspondent"
            id={doc.correspondent ?? null}
            list={correspondents}
          />
        </FactRow>
        <FactRow label="Document type">
          <EntityLink entityType="document_type" id={doc.document_type ?? null} list={docTypes} />
        </FactRow>
        <FactRow label="Storage path">
          <EntityLink
            entityType="storage_path"
            id={doc.storage_path ?? null}
            list={storagePaths}
          />
        </FactRow>
        <FactRow label="Tags">
          {doc.tags.length === 0 ? (
            <span className="text-muted-foreground/60">—</span>
          ) : (
            <span className="flex flex-wrap gap-1">
              {doc.tags.map((t) => (
                <Link key={t} to={entityHref("tag", t)}>
                  <Badge
                    variant="secondary"
                    className="text-primary transition-colors hover:bg-primary/15"
                  >
                    {tags?.find((x) => x.id === t)?.name ?? "…"}
                  </Badge>
                </Link>
              ))}
            </span>
          )}
        </FactRow>
        <FactRow label="Created">{formatDate(doc.created)}</FactRow>
        <FactRow label="Added">{formatDate(doc.added)}</FactRow>
        <FactRow label="ASN">{doc.archive_serial_number ?? "—"}</FactRow>
      </div>
    </div>
  );
}

function TaxonomyFacts({ entity }: { entity: EntityRef }) {
  return (
    <div>
      <FactRow label="Name">{entity.name}</FactRow>
      <FactRow label="Documents">{entity.document_count ?? 0}</FactRow>
      <FactRow label="Matching rule">
        {matchingRule(entity) ?? (
          <span className="text-muted-foreground/60">none</span>
        )}
      </FactRow>
      {entity.is_inbox_tag && (
        <FactRow label="Inbox tag">
          <InboxBadge>yes</InboxBadge>
        </FactRow>
      )}
    </div>
  );
}

/** App-local agent instructions: the agent sees these whenever it works
 * with the entity and must obey them. */
function InstructionsEditor({
  entityType,
  id,
  initial,
}: {
  entityType: string;
  id: number;
  initial: string;
}) {
  const qc = useQueryClient();
  const [text, setText] = useState(initial);
  const save = useMutation({
    mutationFn: () => api.setInstructions(entityType, id, text),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.entity(entityType, id) });
      invalidateEntities(qc, entityType);
    },
  });
  const dirty = text !== initial;
  return (
    <Card className="mb-8 gap-2 p-4">
      <h2 className="text-sm font-medium text-muted-foreground">Agent instructions</h2>
      <p className="text-xs text-muted-foreground/70">
        Local to this application. The agent sees these whenever it works with this{" "}
        {entityType.replaceAll("_", " ")} and is required to follow them.
      </p>
      <Textarea
        aria-label="agent instructions"
        rows={3}
        placeholder="e.g. Only assign this tag to documents from the tax office…"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          Save instructions
        </Button>
        {save.isSuccess && !dirty && (
          <span className="text-xs text-primary">saved</span>
        )}
        {save.error && (
          <span className="text-xs text-destructive">{errorMessage(save.error)}</span>
        )}
      </div>
    </Card>
  );
}

/** The document's two distinct actions — each one visibly starts a
 * session you land in: re-transcribe the text (stops at the review
 * gate), or put the agent to work on the metadata. */
function DocumentActions({ id }: { id: number }) {
  const navigate = useNavigate();
  const start = useMutation({
    mutationFn: (opts: { ocr_only?: boolean; redo_ocr?: boolean }) =>
      api.analyzeDocument(id, opts),
    onSuccess: (s) => navigate(`/sessions/${s.id}`),
  });
  return (
    <span className="flex items-center gap-2">
      <Button
        size="sm"
        variant="outline"
        title="Re-transcribe the document with the vision model; you review the result before anything is written. No metadata analysis."
        disabled={start.isPending}
        onClick={() => start.mutate({ ocr_only: true, redo_ocr: true })}
      >
        <ScanText className="size-3.5" />
        Re-do OCR
      </Button>
      <Button
        size="sm"
        title="Start an analysis session: the agent reads the document and proposes metadata changes, one at a time."
        disabled={start.isPending}
        onClick={() => start.mutate({})}
      >
        <Sparkles className="size-3.5" />
        Start analysis
      </Button>
      {start.error && (
        <span className="text-xs text-destructive">{errorMessage(start.error)}</span>
      )}
    </span>
  );
}

function AnalyzeButton({ entityType, id }: { entityType: string; id: number }) {
  const navigate = useNavigate();
  const analyze = useMutation({
    mutationFn: () => api.analyzeEntity(entityType, id),
    onSuccess: (s) => navigate(`/sessions/${s.id}`),
  });
  return (
    <span className="flex items-center gap-3">
      <Button size="sm" onClick={() => analyze.mutate()} disabled={analyze.isPending}>
        Analyze
      </Button>
      {analyze.error && (
        <span className="text-xs text-destructive">{errorMessage(analyze.error)}</span>
      )}
    </span>
  );
}

/** The stored text layer — what the agent reads, and the basis for
 * judging whether a fresh OCR pass is needed. */
function ContentPanel({ content }: { content: string }) {
  const [openAll, setOpenAll] = useState(false);
  const clamp = 1800;
  const truncated = !openAll && content.length > clamp;
  const shown = truncated ? content.slice(0, clamp) : content;
  return (
    <Card className="mb-8 gap-2 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">
          Content ({content.length.toLocaleString()} characters)
        </h2>
      </div>
      {content.trim() ? (
        <>
          <pre className="max-h-96 overflow-auto rounded-md bg-muted/40 p-3 font-mono text-xs leading-5 whitespace-pre-wrap">
            {shown}
            {truncated && "…"}
          </pre>
          {content.length > clamp && (
            <Button
              variant="ghost"
              size="sm"
              className="self-start text-muted-foreground"
              onClick={() => setOpenAll(!openAll)}
            >
              {openAll ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
              {openAll ? "show less" : "show all"}
            </Button>
          )}
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          No text layer — this document likely needs an OCR pass.
        </p>
      )}
    </Card>
  );
}

const HISTORY_KINDS: Record<string, string> = {
  update_document_metadata: "Metadata updated",
  replace_content: "Content replaced (OCR)",
  create_entity: "Entity created & assigned",
};

function Actor({ actor }: { actor: string }) {
  const name = actor.startsWith("user:") ? actor.slice(5) : actor;
  return (
    <span className="inline-flex items-center gap-1.5">
      <User className="size-3.5 text-muted-foreground" />
      {actor === "system" ? (
        <span className="text-muted-foreground">automatic</span>
      ) : (
        <span className="font-medium">{name}</span>
      )}
    </span>
  );
}

/** Everything this app changed on the document — attributed and linked
 * to the session that produced it. */
function HistorySection({ id }: { id: number }) {
  const { data, error } = useQuery({
    queryKey: keys.documentHistory(id),
    queryFn: () => api.getDocumentHistory(id),
  });
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <LoadingState lines={2} />;
  if (data.length === 0)
    return <EmptyState title="No changes applied to this document yet." />;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-44">When</TableHead>
          <TableHead>Change</TableHead>
          <TableHead className="w-40">By</TableHead>
          <TableHead className="w-64">Session</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.map((h: DocumentHistory) => (
          <TableRow key={h.proposal_id} className={h.reverted ? "opacity-60" : ""}>
            <TableCell className="whitespace-nowrap text-muted-foreground">
              {formatDateTime(h.applied_at)}
            </TableCell>
            <TableCell>
              <span className="flex flex-wrap items-center gap-1.5">
                {HISTORY_KINDS[h.kind] ?? h.kind.replaceAll("_", " ")}
                {h.fields.length > 0 && h.kind !== "replace_content" && (
                  <span className="text-xs text-muted-foreground">
                    ({h.fields.join(", ")})
                  </span>
                )}
                {h.edited && (
                  <Badge variant="secondary" className="font-normal text-muted-foreground">
                    edited
                  </Badge>
                )}
                {h.reverted && (
                  <Badge variant="secondary" className="font-normal text-muted-foreground">
                    reverted
                  </Badge>
                )}
              </span>
            </TableCell>
            <TableCell>
              <Actor actor={h.applied_by} />
            </TableCell>
            <TableCell className="max-w-0">
              {h.session_id != null ? (
                <Link
                  className="block truncate text-primary hover:underline"
                  to={`/sessions/${h.session_id}`}
                >
                  {h.session_title || "session"}
                </Link>
              ) : (
                <span className="text-muted-foreground/60">—</span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export default function EntityPage() {
  const params = useParams();
  // Route is either /documents/:id or /taxonomy/:type/:id.
  const entityType = params.type ?? "document";
  const id = Number(params.id);

  const { data: meta } = useQuery({ queryKey: keys.meta(), queryFn: api.getMeta });
  const docQuery = useQuery({
    queryKey: keys.document(id),
    queryFn: () => api.getDocument(id),
    enabled: entityType === "document",
  });
  const entityQuery = useQuery({
    queryKey: keys.entity(entityType, id),
    queryFn: () => api.getEntity(entityType, id),
    enabled: entityType !== "document",
  });

  const loading = entityType === "document" ? !docQuery.data : !entityQuery.data;
  const error = docQuery.error ?? entityQuery.error;
  if (error) return <ErrorNotice error={error} />;
  if (loading) return <LoadingState lines={5} />;

  const title =
    entityType === "document"
      ? docQuery.data!.title || `Document #${id}`
      : entityQuery.data!.name;

  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <div className="flex-1">
          <p className="text-xs text-muted-foreground/70 capitalize">
            {entityType.replaceAll("_", " ")}
          </p>
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        </div>
        {entityType === "document" ? (
          <DocumentActions id={id} />
        ) : !(entityType === "tag" && entityQuery.data?.is_inbox_tag) ? (
          <AnalyzeButton entityType={entityType} id={id} />
        ) : (
          <span
            className="text-xs text-muted-foreground/70"
            title="The inbox tag is a workflow marker — there is nothing to analyze about it."
          >
            not analyzable (inbox)
          </span>
        )}
        {meta && (
          <a
            className="flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground hover:underline"
            href={paperlessHref(meta.paperless_url, entityType, id)}
            target="_blank"
            rel="noreferrer"
          >
            open in paperless <ExternalLink className="size-3.5" />
          </a>
        )}
      </div>

      <Card className="mb-8 p-4">
        {entityType === "document" ? (
          <DocumentFacts doc={docQuery.data!} />
        ) : (
          <TaxonomyFacts entity={entityQuery.data!} />
        )}
      </Card>

      {entityType === "document" && (
        <ContentPanel content={docQuery.data!.content ?? ""} />
      )}

      {entityType !== "document" && entityQuery.data && (
        <InstructionsEditor
          key={`${entityType}-${id}-${entityQuery.data.instructions ?? ""}`}
          entityType={entityType}
          id={id}
          initial={entityQuery.data.instructions ?? ""}
        />
      )}

      <h2 className="mb-2 text-sm font-medium text-muted-foreground">Sessions</h2>
      <SessionList entityType={entityType} entityId={id} pageSize={5} showEntity={false} />

      {entityType === "document" && (
        <>
          <h2 className="mt-8 mb-2 text-sm font-medium text-muted-foreground">
            Change history
          </h2>
          <HistorySection id={id} />
        </>
      )}
    </div>
  );
}
