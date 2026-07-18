import { useState } from "react";
import { entityName, useTaxonomyLists } from "../hooks/useTaxonomy";
import { InboxBadge } from "../components/StatusBadge";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api, type EntityRef, type PaperlessDocument } from "../api";
import { keys, invalidateEntities } from "../lib/keys";
import { formatDate } from "../lib/format";
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
      <img
        src={`/api/entities/documents/${doc.id}/thumb`}
        alt="document preview"
        className="h-48 w-36 rounded-md border object-cover object-top"
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
        {entity.match ? (
          `${entity.match}`
        ) : (
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

function AnalyzeButton({ entityType, id }: { entityType: string; id: number }) {
  const navigate = useNavigate();
  const [redoOcr, setRedoOcr] = useState(false);
  const analyze = useMutation({
    mutationFn: () =>
      entityType === "document"
        ? api.analyzeDocument(id, { redo_ocr: redoOcr })
        : api.analyzeEntity(entityType, id),
    onSuccess: (s) => navigate(`/sessions/${s.id}`),
  });
  return (
    <span className="flex items-center gap-3">
      {entityType === "document" && (
        <Label className="flex items-center gap-1.5 text-xs font-normal text-muted-foreground">
          <Checkbox checked={redoOcr} onCheckedChange={(v) => setRedoOcr(v === true)} />
          re-do OCR
        </Label>
      )}
      <Button size="sm" onClick={() => analyze.mutate()} disabled={analyze.isPending}>
        Analyze
      </Button>
      {analyze.error && (
        <span className="text-xs text-destructive">{errorMessage(analyze.error)}</span>
      )}
    </span>
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
        {!(entityType === "tag" && entityQuery.data?.is_inbox_tag) ? (
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
    </div>
  );
}
