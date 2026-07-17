import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type EntityRef, type PaperlessDocument, type Proposal } from "../api";
import { StatusBadge } from "./StatusBadge";


// ---------------------------------------------------------------------
// Metadata proposal editor: shows EVERY document field — current value
// in paperless vs. the proposed value — with names resolved (ids stay
// in the background). add_tags/remove_tags are presented as ONE tags
// field; the diff is recomputed on save.
// ---------------------------------------------------------------------

interface Desired {
  title: string;
  correspondent: number | null;
  document_type: number | null;
  storage_path: number | null;
  created: string | null;
  archive_serial_number: number | null;
  tags: number[];
}

function deriveDesired(doc: PaperlessDocument, payload: Record<string, unknown>): Desired {
  const scalar = <T,>(key: string, fallback: T): T =>
    key in payload ? (payload[key] as T) : fallback;
  const removed = new Set((payload.remove_tags as number[] | undefined) ?? []);
  const added = (payload.add_tags as number[] | undefined) ?? [];
  const tags = [...doc.tags.filter((t) => !removed.has(t)), ...added.filter((t) => !doc.tags.includes(t))];
  return {
    title: scalar("title", doc.title),
    correspondent: scalar("correspondent", doc.correspondent),
    document_type: scalar("document_type", doc.document_type),
    storage_path: scalar("storage_path", doc.storage_path),
    created: scalar("created", doc.created?.slice(0, 10) ?? null),
    archive_serial_number: scalar("archive_serial_number", doc.archive_serial_number),
    tags,
  };
}

function buildPayload(
  desired: Desired,
  doc: PaperlessDocument,
  agent: Record<string, unknown>,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    document_id: agent.document_id,
    reason: agent.reason ?? "",
  };
  if (desired.title !== doc.title) payload.title = desired.title;
  if (desired.correspondent !== doc.correspondent) payload.correspondent = desired.correspondent;
  if (desired.document_type !== doc.document_type) payload.document_type = desired.document_type;
  if (desired.storage_path !== doc.storage_path) payload.storage_path = desired.storage_path;
  if (desired.created !== (doc.created?.slice(0, 10) ?? null)) payload.created = desired.created;
  if (desired.archive_serial_number !== doc.archive_serial_number)
    payload.archive_serial_number = desired.archive_serial_number;
  const add = desired.tags.filter((t) => !doc.tags.includes(t));
  const remove = doc.tags.filter((t) => !desired.tags.includes(t));
  if (add.length) payload.add_tags = add;
  if (remove.length) payload.remove_tags = remove;
  return payload;
}

const name = (list: EntityRef[] | undefined, id: number | null | undefined) =>
  id == null ? "—" : (list?.find((e) => e.id === id)?.name ?? `#${id}`);

function Row({
  label,
  current,
  changed,
  agentProposed,
  children,
}: {
  label: string;
  current: string;
  changed: boolean;
  agentProposed: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[10rem_1fr_1.4fr] items-center gap-3 border-b border-zinc-100 py-2">
      <div className="text-sm text-zinc-500">
        {label}
        {agentProposed && (
          <span className="ml-1 rounded bg-blue-50 px-1 py-0.5 text-[10px] text-blue-700">agent</span>
        )}
      </div>
      <div className="truncate text-sm text-zinc-600">{current}</div>
      <div className={changed ? "rounded bg-amber-50 p-1" : "p-1"}>{children}</div>
    </div>
  );
}

function EntitySelect({
  value,
  options,
  onChange,
  disabled,
}: {
  value: number | null;
  options: EntityRef[] | undefined;
  onChange: (v: number | null) => void;
  disabled: boolean;
}) {
  return (
    <select
      className="w-full rounded border border-zinc-200 px-2 py-1 text-sm disabled:bg-zinc-50 disabled:text-zinc-400"
      value={value ?? ""}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
    >
      <option value="">— none —</option>
      {options?.map((o) => (
        <option key={o.id} value={o.id}>
          {o.name}
        </option>
      ))}
    </select>
  );
}

function TagsEditor({
  value,
  options,
  onChange,
  disabled,
}: {
  value: number[];
  options: EntityRef[] | undefined;
  onChange: (v: number[]) => void;
  disabled: boolean;
}) {
  const remaining = (options ?? []).filter((t) => !value.includes(t.id));
  return (
    <div className="flex flex-wrap items-center gap-1">
      {value.map((id) => (
        <span
          key={id}
          className="inline-flex items-center gap-1 rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-800"
        >
          {name(options, id)}
          {!disabled && (
            <button
              aria-label={`remove tag ${name(options, id)}`}
              className="text-emerald-600 hover:text-emerald-900"
              onClick={() => onChange(value.filter((v) => v !== id))}
            >
              ×
            </button>
          )}
        </span>
      ))}
      {!disabled && remaining.length > 0 && (
        <select
          aria-label="add tag"
          className="rounded border border-dashed border-zinc-300 px-1 py-0.5 text-xs text-zinc-500"
          value=""
          onChange={(e) => e.target.value && onChange([...value, Number(e.target.value)])}
        >
          <option value="">+ add tag</option>
          {remaining.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

function MetadataEditor({
  proposal,
  editable,
  onChange,
}: {
  proposal: Proposal;
  editable: boolean;
  onChange: (payload: Record<string, unknown> | null) => void;
}) {
  const effective = proposal.user_payload ?? proposal.agent_payload;
  const docId = effective.document_id as number;
  const { data: doc } = useQuery({
    queryKey: ["document", docId],
    queryFn: () => api.getDocument(docId),
  });
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
  const [edited, setEdited] = useState<Desired | null>(null);

  const initial = useMemo(
    () => (doc ? deriveDesired(doc, effective) : null),
    [doc, effective],
  );
  if (!doc || !initial) return <p className="text-zinc-500">Loading document…</p>;

  const desired = edited ?? initial;
  const update = (patch: Partial<Desired>) => {
    const next = { ...desired, ...patch };
    setEdited(next);
    onChange(buildPayload(next, doc, proposal.agent_payload));
  };
  const inAgent = (k: string) => k in proposal.agent_payload;
  const currentCreated = doc.created?.slice(0, 10) ?? null;

  return (
    <div className="rounded border border-zinc-200 bg-white p-4">
      <div className="grid grid-cols-[10rem_1fr_1.4fr] gap-3 border-b border-zinc-200 pb-1 text-xs uppercase tracking-wide text-zinc-400">
        <div>Field</div>
        <div>Currently in paperless</div>
        <div>Proposed</div>
      </div>
      <Row label="Title" current={doc.title || "—"} changed={desired.title !== doc.title} agentProposed={inAgent("title")}>
        <input
          className="w-full rounded border border-zinc-200 px-2 py-1 text-sm disabled:bg-zinc-50 disabled:text-zinc-400"
          value={desired.title}
          disabled={!editable}
          onChange={(e) => update({ title: e.target.value })}
        />
      </Row>
      <Row
        label="Correspondent"
        current={name(correspondents, doc.correspondent)}
        changed={desired.correspondent !== doc.correspondent}
        agentProposed={inAgent("correspondent")}
      >
        <EntitySelect value={desired.correspondent} options={correspondents} disabled={!editable} onChange={(v) => update({ correspondent: v })} />
      </Row>
      <Row
        label="Document type"
        current={name(docTypes, doc.document_type)}
        changed={desired.document_type !== doc.document_type}
        agentProposed={inAgent("document_type")}
      >
        <EntitySelect value={desired.document_type} options={docTypes} disabled={!editable} onChange={(v) => update({ document_type: v })} />
      </Row>
      <Row
        label="Storage path"
        current={name(storagePaths, doc.storage_path)}
        changed={desired.storage_path !== doc.storage_path}
        agentProposed={inAgent("storage_path")}
      >
        <EntitySelect value={desired.storage_path} options={storagePaths} disabled={!editable} onChange={(v) => update({ storage_path: v })} />
      </Row>
      <Row
        label="Tags"
        current={doc.tags.map((t) => name(tags, t)).join(", ") || "—"}
        changed={JSON.stringify([...desired.tags].sort()) !== JSON.stringify([...doc.tags].sort())}
        agentProposed={inAgent("add_tags") || inAgent("remove_tags")}
      >
        <TagsEditor value={desired.tags} options={tags} disabled={!editable} onChange={(v) => update({ tags: v })} />
      </Row>
      <Row label="Created" current={currentCreated ?? "—"} changed={desired.created !== currentCreated} agentProposed={inAgent("created")}>
        <input
          type="date"
          className="rounded border border-zinc-200 px-2 py-1 text-sm disabled:bg-zinc-50 disabled:text-zinc-400"
          value={desired.created ?? ""}
          disabled={!editable}
          onChange={(e) => update({ created: e.target.value || null })}
        />
      </Row>
    </div>
  );
}

// ---------------------------------------------------------------------
// Generic editor for non-document-metadata kinds.
//
// Left column: paperless values AT PROPOSAL TIME (base_snapshot — what
// the agent looked at; the apply-time staleness check guards against
// paperless having moved since). Right column: the editable proposal.
// Identity fields (ids, entity_type) are never editable and never
// shown as rows — merge context renders as prose from the snapshot.
// ---------------------------------------------------------------------

const HIDDEN = new Set([
  "kind",
  "reason",
  "document_id",
  "entity_type",
  "entity_id",
  "source_id",
  "target_id",
]);

function MergeContext({ p }: { p: Proposal }) {
  const snap = p.base_snapshot as
    | { source?: { name?: string; document_count?: number }; target?: { name?: string; document_count?: number } }
    | null;
  if (p.kind !== "merge_entities" || !snap?.source || !snap?.target) return null;
  return (
    <p className="mb-2 text-sm text-zinc-700">
      Merge <strong>{snap.source.name}</strong>
      <span className="text-zinc-400"> ({snap.source.document_count ?? 0} docs)</span> into{" "}
      <strong>{snap.target.name}</strong>
      <span className="text-zinc-400"> ({snap.target.document_count ?? 0} docs)</span> — the
      target survives, the source is deleted.
    </p>
  );
}

function displayValue(v: unknown): string {
  if (v === undefined) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
}

function parseValue(raw: string, previous: unknown): unknown {
  if (raw === "") return undefined;
  if (typeof previous === "string" || previous === undefined || previous === null) {
    if (!/^[[{"]|^-?\d+(\.\d+)?$|^(true|false|null)$/.test(raw.trim())) return raw;
  }
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function GenericEditor({
  proposal,
  editable,
  onChange,
}: {
  proposal: Proposal;
  editable: boolean;
  onChange: (payload: Record<string, unknown> | null) => void;
}) {
  const effective = proposal.user_payload ?? proposal.agent_payload;
  const [working, setWorking] = useState<Record<string, unknown> | null>(null);
  const current = working ?? effective;
  const snapshot = proposal.base_snapshot ?? {};
  const keys = [
    ...new Set([...Object.keys(proposal.agent_payload), ...Object.keys(current)]),
  ].filter((k) => !HIDDEN.has(k));

  return (
    <div className="rounded border border-zinc-200 bg-white p-4">
      <MergeContext p={proposal} />
      {keys.length > 0 && (
        <table className="w-full table-fixed text-sm">
          <thead>
            <tr className="border-b border-zinc-200 text-left text-xs uppercase tracking-wide text-zinc-400">
              <th className="w-40 py-1 pr-2">Field</th>
              <th className="w-1/3 py-1 pr-2">In paperless (at proposal time)</th>
              <th className="py-1">Proposed</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => {
              const orig = proposal.agent_payload[k];
              const was = snapshot[k];
              const cur = current[k];
              const editedByUser = JSON.stringify(orig) !== JSON.stringify(cur);
              return (
                <tr key={k} className="border-b border-zinc-100 align-top">
                  <td className="py-2 pr-2 font-mono text-xs text-zinc-500">{k}</td>
                  <td className="py-2 pr-2 break-words whitespace-pre-wrap text-zinc-600">
                    {was !== undefined ? displayValue(was) || "—" : "—"}
                  </td>
                  <td className={`py-2 ${editedByUser ? "bg-amber-50" : ""}`}>
                    <input
                      className="w-full rounded border border-zinc-200 px-2 py-1 font-mono text-xs disabled:bg-zinc-50 disabled:text-zinc-400"
                      disabled={!editable}
                      value={displayValue(cur)}
                      onChange={(e) => {
                        const v = parseValue(e.target.value, orig);
                        const next = { ...current };
                        if (v === undefined) delete next[k];
                        else next[k] = v;
                        setWorking(next);
                        // Identity fields always travel from the agent
                        // payload — they are never editable.
                        const identity: Record<string, unknown> = {};
                        for (const f of ["document_id", "entity_type", "entity_id",
                                         "source_id", "target_id"]) {
                          if (f in proposal.agent_payload) identity[f] = proposal.agent_payload[f];
                        }
                        onChange({
                          ...next,
                          ...identity,
                          reason: proposal.agent_payload.reason ?? "",
                        });
                      }}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------
// ProposalCard: the complete review unit (editor + actions). Used on
// the session timeline and on the standalone proposal page.
// ---------------------------------------------------------------------

export function ProposalCard({
  proposal: p,
  archived = false,
}: {
  proposal: Proposal;
  archived?: boolean;
}) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<Record<string, unknown> | null>(null);
  const [editorKey, setEditorKey] = useState(0);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["proposal"] });
    qc.invalidateQueries({ queryKey: ["proposals"] });
    qc.invalidateQueries({ queryKey: ["session"] });
    qc.invalidateQueries({ queryKey: ["document"] });
  };
  const resetEditor = () => {
    setPending(null);
    setEditorKey((k) => k + 1);
  };

  const action = useMutation({
    mutationFn: (a: "reject" | "apply" | "revert") => api.proposalAction(p.id, a),
    onSuccess: invalidate,
  });
  // Would reverting change anything? Greys out the Revert button when
  // paperless already matches the pre-apply state.
  const revertCheck = useQuery({
    queryKey: ["revert-check", p.id],
    queryFn: () => api.revertCheck(p.id),
    enabled: p.applied && !p.reverted,
    staleTime: 10_000,
  });
    const revertNoop = revertCheck.data?.revert_noop === true;
  const save = useMutation({
    mutationFn: (payload: Record<string, unknown> | null) =>
      api.patchProposal(p.id, payload),
    onSuccess: () => {
      resetEditor();
      invalidate();
    },
  });

  // Archived sessions are read-only going FORWARD (no edits, no apply);
  // reverting applied changes stays available.
  const editable = !archived && p.status === "pending";
  const dirty = pending !== null;
  const Editor = p.kind === "update_document_metadata" ? MetadataEditor : GenericEditor;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <span className="font-medium">
          Proposal #{p.id}{" "}
          <span className="text-zinc-400">{p.kind.replaceAll("_", " ")}</span>
        </span>
        <StatusBadge status={p.status} />
        {p.revision > 1 && (
          <span className="text-sm text-zinc-400">
            rev {p.revision} (supersedes #{p.supersedes_id})
          </span>
        )}
      </div>

      {typeof p.agent_payload.reason === "string" && p.agent_payload.reason && (
        <p className="rounded bg-white p-3 text-sm text-zinc-600 shadow-sm">
          <span className="font-medium text-zinc-800">Agent's reasoning: </span>
          {p.agent_payload.reason}
        </p>
      )}
      {p.user_payload && !dirty && (
        <p className="rounded bg-amber-50 p-2 text-xs text-amber-800">
          This proposal has saved user edits — they are what gets applied.
        </p>
      )}

      <Editor key={editorKey} proposal={p} editable={editable} onChange={setPending} />

      <div className="flex items-center gap-2">
        {dirty && (
          <>
            <button
              className="rounded bg-amber-600 px-3 py-1.5 text-sm text-white hover:bg-amber-700"
              onClick={() => save.mutate(pending)}
            >
              Save edits
            </button>
            <button
              className="rounded bg-zinc-200 px-3 py-1.5 text-sm hover:bg-zinc-300"
              onClick={resetEditor}
            >
              Discard
            </button>
          </>
        )}
        {editable && !dirty && (
          <>
            <button
              className="rounded bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-700"
              onClick={() => action.mutate("apply")}
            >
              Apply to paperless
            </button>
            <button
              className="rounded bg-red-600 px-3 py-1.5 text-sm text-white hover:bg-red-700"
              onClick={() => action.mutate("reject")}
            >
              Reject
            </button>
          </>
        )}
        {archived && p.status === "pending" && (
          <span className="text-xs text-zinc-400">
            archived — cannot be applied (unarchive the session first)
          </span>
        )}
        {p.applied && !p.reverted && (
          <button
            className="rounded bg-zinc-200 px-3 py-1.5 text-sm hover:bg-zinc-300 disabled:cursor-not-allowed disabled:opacity-40"
            onClick={() => action.mutate("revert")}
            disabled={revertNoop}
            title={
              revertNoop
                ? "Paperless already matches the state this would restore — there is nothing to undo."
                : "Restore the pre-apply state from the journal"
            }
          >
            Revert
          </button>
        )}
      </div>
      {action.error && <p className="text-sm text-red-600">{String(action.error)}</p>}
      {save.error && <p className="text-sm text-red-600">{String(save.error)}</p>}
    </div>
  );
}
