import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquareText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DateField } from "@/components/app/DateField";
import { SimpleSelect } from "@/components/app/SimpleSelect";
import { Textarea } from "@/components/ui/textarea";
import { ErrorNotice } from "@/components/app/states";
import { api, type EntityRef, type PaperlessDocument, type Proposal } from "../api";
import { keys as qk } from "../lib/keys";
import { formatDate } from "../lib/format";
import { StatusBadge } from "./StatusBadge";
import { errorMessage } from "../lib/errors";

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
  const tags = [
    ...doc.tags.filter((t) => !removed.has(t)),
    ...added.filter((t) => !doc.tags.includes(t)),
  ];
  return {
    title: scalar("title", doc.title),
    correspondent: scalar("correspondent", doc.correspondent ?? null),
    document_type: scalar("document_type", doc.document_type ?? null),
    storage_path: scalar("storage_path", doc.storage_path ?? null),
    created: scalar("created", doc.created?.slice(0, 10) ?? null),
    archive_serial_number: scalar("archive_serial_number", doc.archive_serial_number ?? null),
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
  };
  if (desired.title !== doc.title) payload.title = desired.title;
  if (desired.correspondent !== (doc.correspondent ?? null))
    payload.correspondent = desired.correspondent;
  if (desired.document_type !== (doc.document_type ?? null))
    payload.document_type = desired.document_type;
  if (desired.storage_path !== (doc.storage_path ?? null))
    payload.storage_path = desired.storage_path;
  if (desired.created !== (doc.created?.slice(0, 10) ?? null)) payload.created = desired.created;
  if (desired.archive_serial_number !== (doc.archive_serial_number ?? null))
    payload.archive_serial_number = desired.archive_serial_number;
  const add = desired.tags.filter((t) => !doc.tags.includes(t));
  const remove = doc.tags.filter((t) => !desired.tags.includes(t));
  if (add.length) payload.add_tags = add;
  if (remove.length) payload.remove_tags = remove;
  return payload;
}

const name = (list: EntityRef[] | undefined, id: number | null | undefined) =>
  id == null ? "—" : (list?.find((e) => e.id === id)?.name ?? (list ? "(unknown)" : "…"));

function Row({
  label,
  current,
  changed,
  children,
}: {
  label: string;
  current: string;
  changed: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[10rem_1fr_1.4fr] items-center gap-3 border-b border-border/50 py-2">
      <div className="text-sm text-muted-foreground">{label}</div>
      <div className="truncate text-sm text-muted-foreground">{current}</div>
      <div className={changed ? "rounded-md bg-amber-50 p-1 dark:bg-amber-950/40" : "p-1"}>
        {children}
      </div>
    </div>
  );
}

const NONE = "__none__";

function EntitySelect({
  value,
  options,
  onChange,
  disabled,
  label,
}: {
  value: number | null;
  options: EntityRef[] | undefined;
  onChange: (v: number | null) => void;
  disabled: boolean;
  label: string;
}) {
  return (
    <SimpleSelect
      ariaLabel={label}
      className="w-full"
      disabled={disabled}
      value={value != null ? String(value) : NONE}
      onValueChange={(v) => onChange(v === NONE ? null : Number(v))}
      options={[
        { value: NONE, label: "— none —" },
        ...(options ?? []).map((o) => ({ value: String(o.id), label: o.name })),
      ]}
    />
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
    <div className="flex flex-wrap items-center gap-1.5">
      {value.map((id) => (
        <Badge
          key={id}
          variant="secondary"
          className="gap-1 px-2 py-0.5 text-sm font-normal text-primary"
        >
          {name(options, id)}
          {!disabled && (
            <button
              aria-label={`remove tag ${name(options, id)}`}
              className="opacity-60 hover:opacity-100"
              onClick={() => onChange(value.filter((v) => v !== id))}
            >
              ×
            </button>
          )}
        </Badge>
      ))}
      {!disabled && remaining.length > 0 && (
        <SimpleSelect
          ariaLabel="add tag"
          placeholder="+ add tag"
          value={undefined}
          onValueChange={(v) => onChange([...value, Number(v)])}
          options={remaining.map((t) => ({ value: String(t.id), label: t.name }))}
        />
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
    queryKey: qk.document(docId),
    queryFn: () => api.getDocument(docId),
  });
  const { data: tags } = useQuery({ queryKey: qk.entities("tag"), queryFn: api.listTags });
  const { data: correspondents } = useQuery({
    queryKey: qk.entities("correspondent"),
    queryFn: api.listCorrespondents,
  });
  const { data: docTypes } = useQuery({
    queryKey: qk.entities("document_type"),
    queryFn: api.listDocumentTypes,
  });
  const { data: storagePaths } = useQuery({
    queryKey: qk.entities("storage_path"),
    queryFn: api.listStoragePaths,
  });
  const [edited, setEdited] = useState<Desired | null>(null);

  const initial = useMemo(
    () => (doc ? deriveDesired(doc, effective) : null),
    [doc, effective],
  );
  if (!doc || !initial)
    return <p className="text-sm text-muted-foreground">Loading document…</p>;

  const desired = edited ?? initial;
  const update = (patch: Partial<Desired>) => {
    const next = { ...desired, ...patch };
    setEdited(next);
    onChange(buildPayload(next, doc, proposal.agent_payload));
  };
  const currentCreated = doc.created?.slice(0, 10) ?? null;

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="grid grid-cols-[10rem_1fr_1.4fr] gap-3 border-b pb-1 text-xs tracking-wide text-muted-foreground/70 uppercase">
        <div>Field</div>
        <div>Currently in paperless</div>
        <div>Proposed</div>
      </div>
      <Row
        label="Title"
        current={doc.title || "—"}
        changed={desired.title !== doc.title}
      >
        <Input
          className="h-8"
          value={desired.title}
          disabled={!editable}
          onChange={(e) => update({ title: e.target.value })}
        />
      </Row>
      <Row
        label="Correspondent"
        current={name(correspondents, doc.correspondent)}
        changed={desired.correspondent !== (doc.correspondent ?? null)}
      >
        <EntitySelect
          label="correspondent"
          value={desired.correspondent}
          options={correspondents}
          disabled={!editable}
          onChange={(v) => update({ correspondent: v })}
        />
      </Row>
      <Row
        label="Document type"
        current={name(docTypes, doc.document_type)}
        changed={desired.document_type !== (doc.document_type ?? null)}
      >
        <EntitySelect
          label="document type"
          value={desired.document_type}
          options={docTypes}
          disabled={!editable}
          onChange={(v) => update({ document_type: v })}
        />
      </Row>
      <Row
        label="Storage path"
        current={name(storagePaths, doc.storage_path)}
        changed={desired.storage_path !== (doc.storage_path ?? null)}
      >
        <EntitySelect
          label="storage path"
          value={desired.storage_path}
          options={storagePaths}
          disabled={!editable}
          onChange={(v) => update({ storage_path: v })}
        />
      </Row>
      <Row
        label="Tags"
        current={doc.tags.map((t) => name(tags, t)).join(", ") || "—"}
        changed={
          JSON.stringify([...desired.tags].sort()) !==
          JSON.stringify([...doc.tags].sort())
        }
      >
        <TagsEditor
          value={desired.tags}
          options={tags}
          disabled={!editable}
          onChange={(v) => update({ tags: v })}
        />
      </Row>
      <Row
        label="Created"
        current={formatDate(currentCreated)}
        changed={desired.created !== currentCreated}
      >
        <DateField
          ariaLabel="created date"
          value={desired.created}
          disabled={!editable}
          onChange={(v) => update({ created: v })}
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
  "document_id",
  "entity_type",
  "entity_id",
  "source_id",
  "target_id",
]);

function MergeContext({ p }: { p: Proposal }) {
  const snap = p.base_snapshot as
    | {
        source?: { name?: string; document_count?: number };
        target?: { name?: string; document_count?: number };
      }
    | null;
  if (p.kind !== "merge_entities" || !snap?.source || !snap?.target) return null;
  return (
    <p className="mb-2 text-sm">
      Merge <strong>{snap.source.name}</strong>
      <span className="text-muted-foreground/70"> ({snap.source.document_count ?? 0} docs)</span>{" "}
      into <strong>{snap.target.name}</strong>
      <span className="text-muted-foreground/70"> ({snap.target.document_count ?? 0} docs)</span>{" "}
      — the target survives, the source is deleted.
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
  const fieldKeys = [
    ...new Set([...Object.keys(proposal.agent_payload), ...Object.keys(current)]),
  ].filter((k) => !HIDDEN.has(k));

  return (
    <div className="rounded-lg border bg-card p-4">
      <MergeContext p={proposal} />
      {fieldKeys.length > 0 && (
        <table className="w-full table-fixed text-sm">
          <thead>
            <tr className="border-b text-left text-xs tracking-wide text-muted-foreground/70 uppercase">
              <th className="w-40 py-1 pr-2">Field</th>
              <th className="w-1/3 py-1 pr-2">In paperless (at proposal time)</th>
              <th className="py-1">Proposed</th>
            </tr>
          </thead>
          <tbody>
            {fieldKeys.map((k) => {
              const orig = proposal.agent_payload[k];
              const was = snapshot[k];
              const cur = current[k];
              const editedByUser = JSON.stringify(orig) !== JSON.stringify(cur);
              return (
                <tr key={k} className="border-b border-border/50 align-top">
                  <td className="py-2 pr-2 font-mono text-xs text-muted-foreground">{k}</td>
                  <td className="py-2 pr-2 break-words whitespace-pre-wrap text-muted-foreground">
                    {was !== undefined ? displayValue(was) || "—" : "—"}
                  </td>
                  <td className={`py-2 ${editedByUser ? "rounded-md bg-amber-50 dark:bg-amber-950/40" : ""}`}>
                    <input
                      className="w-full rounded-md border border-input bg-transparent px-2 py-1 font-mono text-xs disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30"
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
                        for (const f of [
                          "document_id",
                          "entity_type",
                          "entity_id",
                          "source_id",
                          "target_id",
                        ]) {
                          if (f in proposal.agent_payload)
                            identity[f] = proposal.agent_payload[f];
                        }
                        onChange({ ...next, ...identity });
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
// Contextual steering: "ask the agent to revise" lives ON the proposal
// being reviewed — the reply arrives as a new turn on the timeline.
// ---------------------------------------------------------------------

function ReviseBox({ proposal: p, onSent }: { proposal: Proposal; onSent: () => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const send = useMutation({
    mutationFn: (text: string) =>
      api.sendMessage(
        p.session_id,
        `About the ${p.kind.replaceAll("_", " ")} proposal: ${text}`,
      ),
    onSuccess: () => {
      setDraft("");
      setOpen(false);
      onSent();
    },
  });
  if (!open) {
    return (
      <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => setOpen(true)}>
        <MessageSquareText className="size-4" />
        Ask the agent to revise…
      </Button>
    );
  }
  return (
    <form
      className="w-full space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (draft.trim()) send.mutate(draft.trim());
      }}
    >
      <Textarea
        aria-label={`revise proposal ${p.id}`}
        autoFocus
        rows={2}
        placeholder="e.g. use the German title from the letterhead, and don't add the 'scan' tag"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={!draft.trim() || send.isPending}>
          Send to agent
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      <ErrorNotice error={send.error} />
    </form>
  );
}

// ---------------------------------------------------------------------
// ProposalCard: the complete review unit (editor + actions + steering).
// ---------------------------------------------------------------------

export function ProposalCard({
  proposal: p,
  archived = false,
  withHeader = true,
}: {
  proposal: Proposal;
  archived?: boolean;
  /** When rendered inside a Panel, the panel owns the header. */
  withHeader?: boolean;
}) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<Record<string, unknown> | null>(null);
  const [editorKey, setEditorKey] = useState(0);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["proposal"] });
    qc.invalidateQueries({ queryKey: qk.proposals() });
    qc.invalidateQueries({ queryKey: ["session"] });
    qc.invalidateQueries({ queryKey: ["document"] });
  };
  const resetEditor = () => {
    setPending(null);
    setEditorKey((k) => k + 1);
  };

  const action = useMutation({
    mutationFn: (a: "apply" | "revert") => api.proposalAction(p.id, a),
    onSuccess: invalidate,
  });
  // Would reverting change anything? Greys out the Revert button when
  // paperless already matches the pre-apply state.
  const revertCheck = useQuery({
    queryKey: qk.revertCheck(p.id),
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
      {withHeader && (
        <div className="flex items-center gap-3">
          <span className="font-medium">
            Proposal{" "}
            <span className="text-muted-foreground/70">{p.kind.replaceAll("_", " ")}</span>
          </span>
          <StatusBadge status={p.status} />
          {p.revision > 1 && (
            <span className="text-sm text-muted-foreground/70">
              revision {p.revision}
            </span>
          )}
        </div>
      )}

      {p.user_payload && !dirty && (
        <p className="rounded-md bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
          This proposal has saved user edits — they are what gets applied.
        </p>
      )}

      <Editor key={editorKey} proposal={p} editable={editable} onChange={setPending} />

      <div className="flex flex-wrap items-center gap-2">
        {dirty && (
          <>
            <Button
              size="sm"
              className="bg-amber-600 text-white hover:bg-amber-700"
              onClick={() => save.mutate(pending)}
            >
              Save edits
            </Button>
            <Button size="sm" variant="secondary" onClick={resetEditor}>
              Discard
            </Button>
          </>
        )}
        {editable && !dirty && (
          <>
            <Button size="sm" onClick={() => action.mutate("apply")}>
              Apply to paperless
            </Button>
            <ReviseBox proposal={p} onSent={invalidate} />
          </>
        )}
        {archived && p.status === "pending" && (
          <span className="text-xs text-muted-foreground/70">
            archived — cannot be applied (unarchive the session first)
          </span>
        )}
        {p.applied && !p.reverted && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => action.mutate("revert")}
            disabled={revertNoop}
            title={
              revertNoop
                ? "Paperless already matches the state this would restore — there is nothing to undo."
                : "Restore the pre-apply state from the journal"
            }
          >
            Revert
          </Button>
        )}
      </div>
      {action.error && (
        <p className="text-sm text-destructive">{errorMessage(action.error)}</p>
      )}
      {save.error && <p className="text-sm text-destructive">{errorMessage(save.error)}</p>}
    </div>
  );
}
