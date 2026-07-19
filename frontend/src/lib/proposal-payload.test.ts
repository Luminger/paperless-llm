import { describe, expect, it } from "vitest";
import {
  buildEntityPayload,
  buildPayload,
  deriveDesired,
  deriveEntityDesired,
  entityRuleProblem,
  fieldKind,
  parseTyped,
} from "./proposal-payload";
import type { PaperlessDocument } from "../api";

const doc = {
  id: 1,
  title: "old title",
  correspondent: 2,
  document_type: null,
  storage_path: null,
  tags: [1, 2],
  created: "2024-04-17T00:00:00Z",
  added: null,
  archive_serial_number: null,
} as unknown as PaperlessDocument;

describe("deriveDesired / buildPayload", () => {
  it("round-trips: derive then build reproduces the payload diff", () => {
    const payload = { title: "new", add_tags: [9], remove_tags: [1] };
    const desired = deriveDesired(doc, payload);
    expect(desired.tags).toEqual([2, 9]);
    const rebuilt = buildPayload(desired, doc, { document_id: 1, ...payload });
    expect(rebuilt).toEqual({
      document_id: 1,
      title: "new",
      add_tags: [9],
      remove_tags: [1],
    });
  });

  it("no changes -> identity-only payload", () => {
    const desired = deriveDesired(doc, {});
    const rebuilt = buildPayload(desired, doc, { document_id: 1 });
    expect(rebuilt).toEqual({ document_id: 1 });
  });
});

describe("typed field coercion (the editor never guesses)", () => {
  it("string fields keep literal 'true' / '123' / 'null' as strings", () => {
    expect(fieldKind("some name")).toBe("string");
    expect(parseTyped("true", "string")).toEqual({ ok: true, value: "true" });
    expect(parseTyped("123", "string")).toEqual({ ok: true, value: "123" });
    expect(parseTyped("null", "string")).toEqual({ ok: true, value: "null" });
  });

  it("number fields parse numbers and reject garbage", () => {
    expect(fieldKind(42)).toBe("number");
    expect(parseTyped("17", "number")).toEqual({ ok: true, value: 17 });
    expect(parseTyped("abc", "number").ok).toBe(false);
  });

  it("boolean and json fields validate", () => {
    expect(fieldKind(true)).toBe("boolean");
    expect(parseTyped("true", "boolean")).toEqual({ ok: true, value: true });
    expect(parseTyped("yes", "boolean").ok).toBe(false);
    expect(fieldKind([1])).toBe("json");
    expect(parseTyped("[1,2]", "json")).toEqual({ ok: true, value: [1, 2] });
    expect(parseTyped("[1,", "json").ok).toBe(false);
  });

  it("empty input clears the field", () => {
    expect(parseTyped("", "string")).toEqual({ ok: true, value: undefined });
  });
});

describe("entity proposal derivation & diff", () => {
  const baseTag = {
    name: "Rechnung",
    match: "",
    matching_algorithm: 0,
    is_insensitive: true,
  };

  it("update: desired overlays the payload on the live entity", () => {
    const d = deriveEntityDesired(
      { entity_type: "tag", entity_id: 5, match: "rechnung", matching_algorithm: 1 },
      baseTag,
    );
    expect(d.name).toBe("Rechnung"); // untouched -> live value
    expect(d.match).toBe("rechnung");
    expect(d.matching_algorithm).toBe(1);
    expect(d.is_insensitive).toBe(true);
  });

  it("update: built payload carries ONLY changed fields + identity", () => {
    const agent = { entity_type: "tag", entity_id: 5, name: "Invoices" };
    const d = deriveEntityDesired(agent, baseTag);
    const p = buildEntityPayload(d, baseTag, agent);
    expect(p).toEqual({ entity_type: "tag", entity_id: 5, name: "Invoices" });
    // user reverts the rename -> nothing but identity remains
    const p2 = buildEntityPayload({ ...d, name: "Rechnung" }, baseTag, agent);
    expect(p2).toEqual({ entity_type: "tag", entity_id: 5 });
  });

  it("create: defaults mirror apply time (auto matching, insensitive)", () => {
    const agent = { entity_type: "correspondent", name: "Telarko", assign_to_documents: [7] };
    const d = deriveEntityDesired(agent, null);
    expect(d.matching_algorithm).toBe(6);
    const p = buildEntityPayload(d, null, agent);
    expect(p).toEqual({
      entity_type: "correspondent",
      name: "Telarko",
      assign_to_documents: [7],
    });
  });

  it("create: an explicit rule travels; switching to auto keeps the agent's explicit key", () => {
    const agent = {
      entity_type: "tag",
      name: "Steuer",
      matching_algorithm: 2,
      match: "steuer finanzamt",
      assign_to_documents: [],
    };
    const d = deriveEntityDesired(agent, null);
    const p = buildEntityPayload(d, null, agent);
    expect(p.matching_algorithm).toBe(2);
    expect(p.match).toBe("steuer finanzamt");
    const auto = buildEntityPayload(
      { ...d, matching_algorithm: 6, match: "" },
      null,
      agent,
    );
    expect(auto.matching_algorithm).toBe(6); // agent proposed one -> stays explicit
    expect(auto.match).toBeUndefined();
  });

  it("rule problems: empty name; pattern modes need a pattern", () => {
    const ok = deriveEntityDesired({ name: "X" }, null);
    expect(entityRuleProblem(ok)).toBeNull();
    expect(entityRuleProblem({ ...ok, name: " " })).toMatch(/name/i);
    expect(entityRuleProblem({ ...ok, matching_algorithm: 4, match: "" })).toMatch(/pattern/);
    expect(entityRuleProblem({ ...ok, matching_algorithm: 4, match: "\\d+" })).toBeNull();
  });
});

describe("custom fields in document proposals", () => {
  const cfDoc = {
    ...doc,
    custom_fields: [
      { field: 1, value: "R-4711" },
      { field: 2, value: "2024-04-17" },
    ],
  } as PaperlessDocument;

  it("derive: doc values overlaid with the payload's (string keys)", () => {
    const d = deriveDesired(cfDoc, {
      document_id: 7,
      custom_fields: { "2": "2024-05-01", "3": true },
    });
    expect(d.custom_fields).toEqual({
      "1": "R-4711",
      "2": "2024-05-01",
      "3": true,
    });
  });

  it("build: only the delta travels; clearing sends null", () => {
    const d = deriveDesired(cfDoc, { document_id: 7 });
    // change field 2, clear field 1, leave everything else
    const payload = buildPayload(
      { ...d, custom_fields: { ...d.custom_fields, "1": null, "2": "2024-06-01" } },
      cfDoc,
      { document_id: 7 },
    );
    expect(payload.custom_fields).toEqual({ "1": null, "2": "2024-06-01" });
    // untouched -> no custom_fields key at all
    const noop = buildPayload(d, cfDoc, { document_id: 7 });
    expect(noop.custom_fields).toBeUndefined();
  });
});
