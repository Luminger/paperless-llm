import { useMemo, useState } from "react";
import { Tip } from "@/components/app/Tip";
import { hasDocumentEditor, hasEntityEditor } from "../lib/proposal-kinds";
import { entityName, useEntityList, useTaxonomyLists } from "../hooks/useTaxonomy";
import type { TaxonomyType } from "../hooks/useTaxonomy";
import { RefChip } from "../features/session/RefChip";
import { matchingName, MATCHING_OPTIONS } from "../lib/matching";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquareText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DateField } from "@/components/app/DateField";
import { SimpleSelect } from "@/components/app/SimpleSelect";
import { Textarea } from "@/components/ui/textarea";
import { ErrorNotice } from "@/components/app/states";
import { api, type CustomFieldDef, type EntityRef, type PaperlessDocument, type Proposal } from "../api";
import { customFieldTypeLabel, displayCustomValue } from "../lib/custom-fields";
import { keys as qk, invalidateProposalEffects } from "../lib/keys";
import { formatDate } from "../lib/format";
import { StatusBadge } from "./StatusBadge";
import {
  buildEntityPayload,
  buildPayload,
  deriveDesired,
  deriveEntityDesired,
  displayValue,
  entityRuleProblem,
  fieldKind,
  docCustomFieldMap,
  parseTyped,
  proposalKindLabel,
  PATTERN_ALGORITHMS,
  type Desired,
  type EntityDesired,
} from "../lib/proposal-payload";

export { proposalKindLabel };

// ---------------------------------------------------------------------
// Metadata proposal editor: shows EVERY document field — current value
// in paperless vs. the proposed value — with names resolved (ids stay
// in the background). add_tags/remove_tags are presented as ONE tags
// field; the diff is recomputed on save.
// ---------------------------------------------------------------------

const name = (list: EntityRef[] | undefined, id: number | null | undefined) =>
  id == null ? "—" : entityName(list, id);

function Row({
  label,
  current,
  changed,
  children,
}: {
  label: string;
  /** Omit for creations — there is no current state to compare. */
  current?: string;
  changed: boolean;
  children: React.ReactNode;
}) {
  if (current === undefined) {
    return (
      <div className="grid grid-cols-[10rem_1fr] items-center gap-3 border-b border-border/50 py-2">
        <div className="text-sm text-muted-foreground">{label}</div>
        <div className={changed ? "rounded-md bg-warning/10 p-1" : "p-1"}>
          {children}
        </div>
      </div>
    );
  }
  return (
    <div className="grid grid-cols-[10rem_1fr_1.4fr] items-center gap-3 border-b border-border/50 py-2">
      <div className="text-sm text-muted-foreground">{label}</div>
      <div className="truncate text-sm text-muted-foreground">{current}</div>
      <div className={changed ? "rounded-md bg-warning/10 p-1" : "p-1"}>
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
            <Tip content="Remove">
              <button
                aria-label={`remove tag ${name(options, id)}`}
                className="opacity-60 hover:opacity-100"
                onClick={() => onChange(value.filter((v) => v !== id))}
              >
                ×
              </button>
            </Tip>
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
  const { tags, correspondents, docTypes, storagePaths } = useTaxonomyLists();
  const { data: fieldDefs } = useQuery({
    queryKey: qk.customFields(),
    queryFn: api.listCustomFields,
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
      <CustomFieldRows
        doc={doc}
        defs={fieldDefs}
        desired={desired.custom_fields}
        editable={editable}
        onChange={(cf) => update({ custom_fields: cf })}
      />
    </div>
  );
}

/** Custom-field rows: one per field the document carries or the
 * proposal touches, each with the widget its data_type calls for —
 * plus a picker to set a value on any other defined field. */
function CustomFieldRows({
  doc,
  defs,
  desired,
  editable,
  onChange,
}: {
  doc: PaperlessDocument;
  defs: CustomFieldDef[] | undefined;
  desired: Record<string, unknown>;
  editable: boolean;
  onChange: (cf: Record<string, unknown>) => void;
}) {
  const docCf = docCustomFieldMap(doc);
  const shown = [
    ...new Set([...Object.keys(docCf), ...Object.keys(desired)]),
  ].filter((k) => docCf[k] != null || desired[k] != null);
  const byId = new Map((defs ?? []).map((d) => [String(d.id), d]));
  const addable = (defs ?? []).filter(
    (d) => !shown.includes(String(d.id)) && d.data_type !== "documentlink",
  );
  if (shown.length === 0 && (!editable || addable.length === 0)) return null;
  return (
    <>
      {shown.map((k) => {
        const def = byId.get(k);
        const cur = docCf[k] ?? null;
        const want = desired[k] ?? null;
        return (
          <Row
            key={k}
            label={def?.name ?? `Custom field ${k}`}
            current={displayCustomValue(def, cur)}
            changed={JSON.stringify(want) !== JSON.stringify(cur)}
          >
            <div className="flex items-center gap-1.5">
              <CustomValueInput
                def={def}
                value={want}
                disabled={!editable}
                onChange={(v) => onChange({ ...desired, [k]: v })}
              />
              {editable && want != null && (
                <Tip content="Clear this value">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7 shrink-0 text-muted-foreground"
                    aria-label={`clear ${def?.name ?? k}`}
                    onClick={() => onChange({ ...desired, [k]: null })}
                  >
                    ×
                  </Button>
                </Tip>
              )}
            </div>
          </Row>
        );
      })}
      {editable && addable.length > 0 && (
        <div className="pt-2">
          <SimpleSelect
            ariaLabel="set a custom field"
            placeholder="+ set custom field"
            value={undefined}
            onValueChange={(v) => {
              const def = byId.get(v);
              onChange({
                ...desired,
                [v]: def?.data_type === "boolean" ? false : "",
              });
            }}
            options={addable.map((d) => ({
              value: String(d.id),
              label: `${d.name} (${customFieldTypeLabel(d.data_type)})`,
            }))}
          />
        </div>
      )}
    </>
  );
}

/** The widget a custom-field data_type calls for. */
function CustomValueInput({
  def,
  value,
  disabled,
  onChange,
}: {
  def: CustomFieldDef | undefined;
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
}) {
  const label = def?.name ?? "custom field";
  switch (def?.data_type) {
    case "boolean":
      return (
        <SimpleSelect
          ariaLabel={label}
          className="w-full"
          disabled={disabled}
          value={value === true ? "yes" : value === false ? "no" : undefined}
          placeholder="—"
          onValueChange={(v) => onChange(v === "yes")}
          options={[
            { value: "yes", label: "yes" },
            { value: "no", label: "no" },
          ]}
        />
      );
    case "date":
      return (
        <DateField
          ariaLabel={label}
          value={value == null ? null : String(value).slice(0, 10)}
          disabled={disabled}
          onChange={(v) => onChange(v)}
        />
      );
    case "select":
      return (
        <SimpleSelect
          ariaLabel={label}
          className="w-full"
          disabled={disabled}
          value={value == null ? undefined : String(value)}
          placeholder="—"
          onValueChange={(v) => onChange(v)}
          options={(def.select_options ?? []).map((o) => ({
            value: String(o.id),
            label: String(o.label ?? o.id),
          }))}
        />
      );
    case "integer":
    case "float":
      return (
        <Input
          aria-label={label}
          type="number"
          className="h-8"
          step={def.data_type === "integer" ? 1 : "any"}
          value={value == null ? "" : String(value)}
          disabled={disabled}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") return onChange(null);
            const n = Number(raw);
            if (Number.isFinite(n))
              onChange(def.data_type === "integer" ? Math.trunc(n) : n);
          }}
        />
      );
    case "documentlink": {
      const n = Array.isArray(value) ? value.length : 0;
      return (
        <span className="text-sm text-muted-foreground">
          {n} linked document{n === 1 ? "" : "s"} (edited in paperless)
        </span>
      );
    }
    default:
      // string / url / monetary — free text
      return (
        <Input
          aria-label={label}
          className="h-8"
          value={value == null ? "" : String(value)}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
        />
      );
  }
}

// ---------------------------------------------------------------------
// Entity proposal editors (create/update/merge/delete): the SAME
// standard as the document editor — named fields, live current values,
// typed widgets. Raw ids, nulls and algorithm numbers stay backstage.
// ---------------------------------------------------------------------


/** Chips of document titles (via the shared RefChip — tooltip + link),
 * removable while editable, extendable via a small title search. */
function AssignDocsEditor({
  value,
  onChange,
  disabled,
}: {
  value: number[];
  onChange: (v: number[]) => void;
  disabled: boolean;
}) {
  const [q, setQ] = useState("");
  const search = useQuery({
    queryKey: qk.documents({ query: q, page_size: 8 }, 1),
    queryFn: () => api.listDocuments({ query: q, page_size: 8 }),
    enabled: q.trim().length >= 2,
  });
  const addable = (search.data?.results ?? []).filter(
    (d) => !value.includes(d.id),
  );
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        {value.length === 0 && (
          <span className="text-sm text-muted-foreground">— none —</span>
        )}
        {value.map((id) => (
          <span key={id} className="inline-flex items-center gap-0.5">
            <RefChip type="document" id={id} />
            {!disabled && (
              <Tip content="Remove">
                <button
                  aria-label={`remove document ${id}`}
                  className="text-muted-foreground opacity-60 hover:opacity-100"
                  onClick={() => onChange(value.filter((v) => v !== id))}
                >
                  ×
                </button>
              </Tip>
            )}
          </span>
        ))}
      </div>
      {!disabled && (
        <div className="relative">
          <Input
            className="h-8"
            placeholder="Search documents to add…"
            aria-label="search documents to assign"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          {q.trim().length >= 2 && addable.length > 0 && (
            <ul className="absolute z-10 mt-1 w-full rounded-md border bg-popover p-1 shadow-md">
              {addable.map((d) => (
                <li key={d.id}>
                  <button
                    className="w-full truncate rounded px-2 py-1 text-left text-sm hover:bg-accent"
                    onClick={() => {
                      onChange([...value, d.id]);
                      setQ("");
                    }}
                  >
                    {d.title || "(untitled)"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/** create_entity / update_entity: name + matching rule + (create only)
 * document assignment, diffed against the LIVE entity like the
 * document editor diffs against the live document. */
function EntityRuleEditor({
  proposal,
  editable,
  onChange,
}: {
  proposal: Proposal;
  editable: boolean;
  onChange: (payload: Record<string, unknown> | null) => void;
}) {
  const effective = proposal.user_payload ?? proposal.agent_payload;
  const isCreate = proposal.kind === "create_entity";
  const entityType = effective.entity_type as TaxonomyType;
  const entityId = effective.entity_id as number | undefined;
  const { data: list } = useEntityList(entityType);
  const base = isCreate ? null : (list ?? []).find((e) => e.id === entityId);
  const [edited, setEdited] = useState<EntityDesired | null>(null);

  const initial = useMemo(
    () =>
      isCreate || base ? deriveEntityDesired(effective, base ?? null) : null,
    [isCreate, base, effective],
  );
  if (!initial)
    return (
      <p className="text-sm text-muted-foreground">
        {list && !base
          ? "This entity no longer exists in paperless."
          : "Loading entity…"}
      </p>
    );

  const desired = edited ?? initial;
  const problem = entityRuleProblem(desired);
  const update = (patch: Partial<EntityDesired>) => {
    const next = { ...desired, ...patch };
    setEdited(next);
    // Invalid states stay local (red hint below) — the last VALID
    // payload remains what Save would persist, like FieldInput does.
    if (entityRuleProblem(next) == null)
      onChange(buildEntityPayload(next, base ?? null, proposal.agent_payload));
  };
  const usesPattern = PATTERN_ALGORITHMS.has(desired.matching_algorithm);
  const cur = (v: string | undefined) => (isCreate ? undefined : v || "—");

  return (
    <div className="rounded-lg border bg-card p-4">
      <div
        className={`grid ${isCreate ? "grid-cols-[10rem_1fr]" : "grid-cols-[10rem_1fr_1.4fr]"} gap-3 border-b pb-1 text-xs tracking-wide text-muted-foreground/70 uppercase`}
      >
        <div>Field</div>
        {!isCreate && <div>Currently in paperless</div>}
        <div>Proposed</div>
      </div>
      <Row
        label="Name"
        current={cur(base?.name)}
        changed={!isCreate && desired.name !== (base?.name ?? "")}
      >
        <Input
          aria-label="entity name"
          className="h-8"
          value={desired.name}
          disabled={!editable}
          onChange={(e) => update({ name: e.target.value })}
        />
      </Row>
      <Row
        label="Auto-assignment"
        current={cur(matchingName(base?.matching_algorithm))}
        changed={
          !isCreate &&
          desired.matching_algorithm !== (base?.matching_algorithm ?? 0)
        }
      >
        <SimpleSelect
          ariaLabel="matching mode"
          className="w-full"
          disabled={!editable}
          value={String(desired.matching_algorithm)}
          onValueChange={(v) => update({ matching_algorithm: Number(v) })}
          options={MATCHING_OPTIONS}
        />
      </Row>
      {(usesPattern || (base != null && Boolean(base.match))) && (
        <Row
          label="Match pattern"
          current={cur(base?.match)}
          changed={!isCreate && desired.match !== (base?.match ?? "")}
        >
          <Input
            aria-label="match pattern"
            className="h-8"
            placeholder={usesPattern ? "e.g. Telarko" : "not used by this mode"}
            value={desired.match}
            disabled={!editable || !usesPattern}
            onChange={(e) => update({ match: e.target.value })}
          />
        </Row>
      )}
      {usesPattern && (
        <Row
          label="Case"
          current={cur(base ? (base.is_insensitive ? "ignore case" : "match case") : undefined)}
          changed={!isCreate && desired.is_insensitive !== (base?.is_insensitive ?? true)}
        >
          <SimpleSelect
            ariaLabel="case sensitivity"
            className="w-full"
            disabled={!editable}
            value={desired.is_insensitive ? "i" : "s"}
            onValueChange={(v) => update({ is_insensitive: v === "i" })}
            options={[
              { value: "i", label: "Ignore case" },
              { value: "s", label: "Match case exactly" },
            ]}
          />
        </Row>
      )}
      {isCreate && (
        <Row label="Assign to documents" changed={false}>
          <AssignDocsEditor
            value={desired.assign_to_documents}
            disabled={!editable}
            onChange={(v) => update({ assign_to_documents: v })}
          />
        </Row>
      )}
      {problem && editable && (
        <p className="mt-2 text-xs text-destructive">
          {problem} Fix it to save — until then the last valid state is
          what Save persists.
        </p>
      )}
    </div>
  );
}

/** delete_entity: prose from the snapshot + the one option. */
function DeleteEntityEditor({
  proposal,
  editable,
  onChange,
}: {
  proposal: Proposal;
  editable: boolean;
  onChange: (payload: Record<string, unknown> | null) => void;
}) {
  const effective = proposal.user_payload ?? proposal.agent_payload;
  const snap = proposal.base_snapshot as
    | { name?: string; document_count?: number }
    | null;
  const [force, setForce] = useState(Boolean(effective.force));
  const docs = snap?.document_count ?? 0;
  return (
    <div className="rounded-lg border bg-card p-4">
      <p className="text-sm">
        Delete <strong>{snap?.name ?? "this entity"}</strong>
        <span className="text-muted-foreground/70">
          {" "}
          ({docs} document{docs === 1 ? "" : "s"} at proposal time)
        </span>
        {" "}— this cannot be reverted from the journal.
      </p>
      <Row label="If still in use" changed={force !== Boolean(effective.force)}>
        <SimpleSelect
          ariaLabel="delete behavior"
          className="w-full"
          disabled={!editable}
          value={force ? "force" : "refuse"}
          onValueChange={(v) => {
            const f = v === "force";
            setForce(f);
            onChange({
              entity_type: proposal.agent_payload.entity_type,
              entity_id: proposal.agent_payload.entity_id,
              force: f,
            });
          }}
          options={[
            { value: "refuse", label: "Refuse — keep it if documents still use it" },
            { value: "force", label: "Detach it from all documents first" },
          ]}
        />
      </Row>
    </div>
  );
}

/** Dispatch inside the entity family. merge_entities is prose-only —
 * its context sentence IS the whole review. */
function EntityEditor(props: {
  proposal: Proposal;
  editable: boolean;
  onChange: (payload: Record<string, unknown> | null) => void;
}) {
  const kind = props.proposal.kind;
  if (kind === "merge_entities") {
    return (
      <div className="rounded-lg border bg-card p-4">
        <MergeContext p={props.proposal} />
      </div>
    );
  }
  if (kind === "delete_entity") return <DeleteEntityEditor {...props} />;
  return <EntityRuleEditor {...props} />;
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

/** Human label naming the entity type: "create document type",
 * "update tag", "merge correspondents", "update document metadata". */
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

/** One payload field editor whose widget follows the field's TYPE
 * (string/number/boolean/json) — entered text is never re-guessed. */
function FieldInput({
  label,
  value,
  kind,
  editable,
  onCommit,
}: {
  label: string;
  value: string;
  kind: ReturnType<typeof fieldKind>;
  editable: boolean;
  onCommit: (v: unknown) => void;
}) {
  const [raw, setRaw] = useState(value);
  const [invalid, setInvalid] = useState(false);
  const handle = (text: string) => {
    setRaw(text);
    const parsed = parseTyped(text, kind);
    setInvalid(!parsed.ok);
    if (parsed.ok) onCommit(parsed.value);
  };
  if (kind === "boolean") {
    return (
      <SimpleSelect
        ariaLabel={label}
        value={raw || "false"}
        onValueChange={handle}
        disabled={!editable}
        options={[
          { value: "true", label: "true" },
          { value: "false", label: "false" },
        ]}
      />
    );
  }
  const cls = `w-full rounded-md border px-2 py-1 font-mono text-xs disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 ${
    invalid ? "border-destructive/60 bg-destructive/10" : "border-input bg-transparent"
  }`;
  if (kind === "json") {
    return (
      <textarea
        aria-label={label}
        className={`${cls} min-h-16`}
        disabled={!editable}
        value={raw}
        onChange={(e) => handle(e.target.value)}
      />
    );
  }
  return (
    <input
      aria-label={label}
      type={kind === "number" ? "number" : "text"}
      className={cls}
      disabled={!editable}
      value={raw}
      onChange={(e) => handle(e.target.value)}
    />
  );
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
  // A brand-new entity has no paperless state — the column would
  // always be empty, so it isn't shown.
  const isCreate = proposal.kind === "create_entity";
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
              {!isCreate && <th className="w-1/3 py-1 pr-2">In paperless (at proposal time)</th>}
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
                  {!isCreate && (
                    <td className="py-2 pr-2 break-words whitespace-pre-wrap text-muted-foreground">
                      {was !== undefined ? displayValue(was) || "—" : "—"}
                    </td>
                  )}
                  <td className={`py-2 ${editedByUser ? "rounded-md bg-warning/10" : ""}`}>
                    <FieldInput
                      label={k}
                      value={displayValue(cur)}
                      kind={fieldKind(orig)}
                      editable={editable}
                      onCommit={(v) => {
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
  onDirtyChange,
}: {
  proposal: Proposal;
  archived?: boolean;
  /** When rendered inside a Panel, the panel owns the header. */
  withHeader?: boolean;
  /** The wrapping panel refuses to self-fold while an edit is open. */
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const qc = useQueryClient();
  const [pending, setPendingRaw] = useState<Record<string, unknown> | null>(null);
  const [editorKey, setEditorKey] = useState(0);
  const setPending = (v: Record<string, unknown> | null) => {
    setPendingRaw(v);
    onDirtyChange?.(v !== null);
  };

  const invalidate = () => invalidateProposalEffects(qc, p);
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
  // AUDIT FS-5: the proposal can be decided UNDER the user (auto
  // policy, second tab). Editing affordances follow `editable`, not
  // just `dirty` — a PATCH against a decided proposal must be
  // impossible, and the user deserves a word about what happened.
  const decidedWhileEditing = dirty && !editable;
  const Editor = hasDocumentEditor(p.kind)
    ? MetadataEditor
    : hasEntityEditor(p.kind)
      ? EntityEditor
      : GenericEditor;

  return (
    <div className="space-y-3">
      {withHeader && (
        <div className="flex items-center gap-3">
          <span className="font-medium">
            Proposal{" "}
            <span className="text-muted-foreground/70">{proposalKindLabel(p)}</span>
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
        <p className="rounded-md bg-warning/10 p-2 text-xs text-warning">
          This proposal has saved user edits — they are what gets applied.
        </p>
      )}

      <Editor key={editorKey} proposal={p} editable={editable} onChange={setPending} />

      {decidedWhileEditing && (
        <p className="rounded-md bg-warning/10 p-2 text-xs text-warning">
          This proposal was decided while you were editing — your unsaved
          changes cannot be applied anymore.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {dirty && editable && (
          <>
            <Button
              size="sm"
              className="bg-warning text-white hover:bg-warning/90"
              onClick={() => save.mutate(pending)}
            >
              Save edits
            </Button>
            <Button size="sm" variant="secondary" onClick={resetEditor}>
              Discard
            </Button>
          </>
        )}
        {decidedWhileEditing && (
          <Button size="sm" variant="secondary" onClick={resetEditor}>
            Discard my edits
          </Button>
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
          <Tip
            mayDisable
            content={
              revertNoop
                ? "Paperless already matches the state this would restore — there is nothing to undo."
                : "Restore the pre-apply state from the journal"
            }
          >
            <Button
              size="sm"
              variant="secondary"
              onClick={() => action.mutate("revert")}
              disabled={revertNoop}
            >
              Revert
            </Button>
          </Tip>
        )}
      </div>
      <ErrorNotice error={action.error} />
      <ErrorNotice error={save.error} />
    </div>
  );
}
