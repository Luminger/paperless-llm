// Pure proposal-payload logic (no React): derivation of the desired
// document state, payload diffs, kind labels, and the typed field
// coercion for the generic editor. Unit-tested directly.

import type { PaperlessDocument, Proposal } from "../api";

export interface Desired {
  title: string;
  correspondent: number | null;
  document_type: number | null;
  storage_path: number | null;
  created: string | null;
  archive_serial_number: number | null;
  tags: number[];
}

export function deriveDesired(doc: PaperlessDocument, payload: Record<string, unknown>): Desired {
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

export function buildPayload(
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

export function proposalKindLabel(p: Proposal): string {
  const entity = (
    (p.agent_payload.entity_type as string | undefined) ??
    p.entity_type ??
    "entity"
  ).replaceAll("_", " ");
  switch (p.kind) {
    case "create_entity":
      return `create ${entity}`;
    case "update_entity":
      return `update ${entity}`;
    case "delete_entity":
      return `delete ${entity}`;
    case "merge_entities":
      return `merge ${entity}s`;
    default:
      return p.kind.replaceAll("_", " ");
  }
}

export function displayValue(v: unknown): string {
  if (v === undefined) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** What kind of editor a payload field needs — decided by the TYPE the
 * agent emitted, never by guessing at the entered text. Typing the
 * literal "true" into a name field stays the string "true". */
export type FieldKind = "string" | "number" | "boolean" | "json";

export function fieldKind(orig: unknown): FieldKind {
  if (typeof orig === "number") return "number";
  if (typeof orig === "boolean") return "boolean";
  if (orig !== null && typeof orig === "object") return "json";
  return "string";
}

/** Parse the entered text according to the field's declared kind.
 * ok=false means "not committable yet" (e.g. invalid JSON). */
export function parseTyped(
  raw: string,
  kind: FieldKind,
): { ok: boolean; value: unknown } {
  if (raw === "") return { ok: true, value: undefined };
  switch (kind) {
    case "string":
      return { ok: true, value: raw };
    case "number": {
      const n = Number(raw);
      return Number.isFinite(n) ? { ok: true, value: n } : { ok: false, value: undefined };
    }
    case "boolean":
      if (raw === "true") return { ok: true, value: true };
      if (raw === "false") return { ok: true, value: false };
      return { ok: false, value: undefined };
    case "json":
      try {
        return { ok: true, value: JSON.parse(raw) };
      } catch {
        return { ok: false, value: undefined };
      }
  }
}

// ---------------------------------------------------------------------
// Entity proposals (create/update): same derivation/diff contract as
// the document editor above — desired state overlays the payload on the
// live base; the built payload carries ONLY fields that differ (plus
// identity), preserving the exclude-unset apply semantics.
// ---------------------------------------------------------------------

/** Paperless matching-rule state an entity proposal can shape. */
export interface EntityDesired {
  name: string;
  match: string;
  matching_algorithm: number;
  is_insensitive: boolean;
  assign_to_documents: number[];
}

/** Rule fields shared by create_entity and update_entity payloads. */
interface EntityBase {
  name: string;
  match?: string;
  matching_algorithm?: number;
  is_insensitive?: boolean;
}

/** Algorithms that need a `match` pattern (1=any word … 5=fuzzy);
 * 0=none and 6=auto don't use one. */
export const PATTERN_ALGORITHMS = new Set([1, 2, 3, 4, 5]);

export function deriveEntityDesired(
  payload: Record<string, unknown>,
  base: EntityBase | null,
): EntityDesired {
  const scalar = <T,>(key: string, fallback: T): T =>
    key in payload && payload[key] !== null ? (payload[key] as T) : fallback;
  return {
    name: scalar("name", base?.name ?? ""),
    match: scalar("match", base?.match ?? ""),
    // A create without an explicit rule defaults to auto (ML) at apply
    // time — mirror that so the editor shows what will happen.
    matching_algorithm: scalar(
      "matching_algorithm",
      base?.matching_algorithm ?? 6,
    ),
    is_insensitive: scalar("is_insensitive", base?.is_insensitive ?? true),
    assign_to_documents: scalar("assign_to_documents", [] as number[]),
  };
}

/** Why the desired rule cannot be committed, or null when fine. */
export function entityRuleProblem(desired: EntityDesired): string | null {
  if (!desired.name.trim()) return "A name is required.";
  if (PATTERN_ALGORITHMS.has(desired.matching_algorithm) && !desired.match.trim())
    return "This matching mode needs a pattern.";
  return null;
}

export function buildEntityPayload(
  desired: EntityDesired,
  base: EntityBase | null,
  agent: Record<string, unknown>,
): Record<string, unknown> {
  const payload: Record<string, unknown> = { entity_type: agent.entity_type };
  if (base) {
    // update_entity: identity + changed fields only.
    payload.entity_id = agent.entity_id;
    if (desired.name !== base.name) payload.name = desired.name;
    if (desired.match !== (base.match ?? "")) payload.match = desired.match;
    if (desired.matching_algorithm !== (base.matching_algorithm ?? 0))
      payload.matching_algorithm = desired.matching_algorithm;
    if (desired.is_insensitive !== (base.is_insensitive ?? true))
      payload.is_insensitive = desired.is_insensitive;
    return payload;
  }
  // create_entity: name + docs always; the rule only when it deviates
  // from the apply-time default (auto, case-insensitive, no pattern) or
  // the agent already proposed it (keep its explicit choice visible).
  payload.name = desired.name;
  payload.assign_to_documents = desired.assign_to_documents;
  if (desired.match.trim()) payload.match = desired.match;
  if (desired.matching_algorithm !== 6 || "matching_algorithm" in agent)
    payload.matching_algorithm = desired.matching_algorithm;
  if (!desired.is_insensitive || "is_insensitive" in agent)
    payload.is_insensitive = desired.is_insensitive;
  return payload;
}
