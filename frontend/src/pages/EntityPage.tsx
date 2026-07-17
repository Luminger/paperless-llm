import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type EntityRef, type PaperlessDocument } from "../api";
import { SessionList } from "../components/SessionList";

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
  switch (entityType) {
    case "document":
      return `${base}/documents/${id}/details`;
    case "tag":
      return `${base}/documents?tags__id__all=${id}`;
    case "correspondent":
      return `${base}/documents?correspondent__id=${id}`;
    case "document_type":
      return `${base}/documents?document_type__id=${id}`;
    default:
      return base;
  }
}

function FactRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[10rem_1fr] gap-3 border-b border-zinc-100 py-2 text-sm">
      <div className="text-zinc-500">{label}</div>
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
  if (id == null) return <span className="text-zinc-400">—</span>;
  const name = list?.find((e) => e.id === id)?.name ?? `#${id}`;
  return (
    <Link className="text-emerald-700 hover:underline" to={entityHref(entityType, id)}>
      {name}
    </Link>
  );
}

function DocumentFacts({ doc }: { doc: PaperlessDocument }) {
  const { data: tags } = useQuery({ queryKey: ["tags"], queryFn: api.listTags });
  const { data: correspondents } = useQuery({
    queryKey: ["correspondents"],
    queryFn: api.listCorrespondents,
  });
  const { data: docTypes } = useQuery({
    queryKey: ["document_types"],
    queryFn: api.listDocumentTypes,
  });
  const { data: storagePaths } = useQuery({
    queryKey: ["storage_paths"],
    queryFn: api.listStoragePaths,
  });
  return (
    <div className="flex gap-6">
      <img
        src={`/api/entities/documents/${doc.id}/thumb`}
        alt="document preview"
        className="h-48 w-36 rounded border border-zinc-200 object-cover object-top"
      />
      <div className="flex-1">
        <FactRow label="Title">{doc.title || "—"}</FactRow>
        <FactRow label="Correspondent">
          <EntityLink entityType="correspondent" id={doc.correspondent} list={correspondents} />
        </FactRow>
        <FactRow label="Document type">
          <EntityLink entityType="document_type" id={doc.document_type} list={docTypes} />
        </FactRow>
        <FactRow label="Storage path">
          <EntityLink entityType="storage_path" id={doc.storage_path} list={storagePaths} />
        </FactRow>
        <FactRow label="Tags">
          {doc.tags.length === 0 ? (
            <span className="text-zinc-400">—</span>
          ) : (
            <span className="flex flex-wrap gap-1">
              {doc.tags.map((t) => (
                <Link
                  key={t}
                  to={entityHref("tag", t)}
                  className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-800 hover:bg-emerald-200"
                >
                  {tags?.find((x) => x.id === t)?.name ?? `#${t}`}
                </Link>
              ))}
            </span>
          )}
        </FactRow>
        <FactRow label="Created">{doc.created?.slice(0, 10) ?? "—"}</FactRow>
        <FactRow label="Added">{doc.added?.slice(0, 10) ?? "—"}</FactRow>
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
        {entity.match ? `${entity.match}` : <span className="text-zinc-400">none</span>}
      </FactRow>
      {entity.is_inbox_tag && (
        <FactRow label="Inbox tag">
          <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-700">yes</span>
        </FactRow>
      )}
    </div>
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
        <label className="flex items-center gap-1.5 text-xs text-zinc-500">
          <input
            type="checkbox"
            checked={redoOcr}
            onChange={(e) => setRedoOcr(e.target.checked)}
          />
          re-do OCR
        </label>
      )}
      <button
        className="rounded bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-700 disabled:opacity-50"
        onClick={() => analyze.mutate()}
        disabled={analyze.isPending}
      >
        Analyze
      </button>
      {analyze.error && <span className="text-xs text-red-600">{String(analyze.error)}</span>}
    </span>
  );
}

export default function EntityPage() {
  const params = useParams();
  // Route is either /documents/:id or /taxonomy/:type/:id.
  const entityType = params.type ?? "document";
  const id = Number(params.id);

  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: api.getMeta });
  const docQuery = useQuery({
    queryKey: ["document", id],
    queryFn: () => api.getDocument(id),
    enabled: entityType === "document",
  });
  const entityQuery = useQuery({
    queryKey: ["entity", entityType, id],
    queryFn: () => api.getEntity(entityType, id),
    enabled: entityType !== "document",
  });

  const loading = entityType === "document" ? !docQuery.data : !entityQuery.data;
  const error = docQuery.error ?? entityQuery.error;
  if (error) return <p className="text-red-600">{String(error)}</p>;
  if (loading) return <p className="text-zinc-500">Loading…</p>;

  const title =
    entityType === "document"
      ? docQuery.data!.title || `Document #${id}`
      : entityQuery.data!.name;

  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <div className="flex-1">
          <p className="text-xs text-zinc-400 capitalize">{entityType.replaceAll("_", " ")}</p>
          <h1 className="text-xl font-semibold">{title}</h1>
        </div>
        <AnalyzeButton entityType={entityType} id={id} />
        {meta && (
          <a
            className="text-sm text-zinc-500 hover:text-zinc-800 hover:underline"
            href={paperlessHref(meta.paperless_url, entityType, id)}
            target="_blank"
            rel="noreferrer"
          >
            open in paperless ↗
          </a>
        )}
      </div>

      <div className="mb-8 rounded border border-zinc-200 bg-white p-4">
        {entityType === "document" ? (
          <DocumentFacts doc={docQuery.data!} />
        ) : (
          <TaxonomyFacts entity={entityQuery.data!} />
        )}
      </div>

      <h2 className="mb-2 text-sm font-medium text-zinc-600">Sessions</h2>
      <SessionList entityType={entityType} entityId={id} pageSize={5} showEntity={false} />
    </div>
  );
}
