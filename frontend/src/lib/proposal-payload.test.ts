import { describe, expect, it } from "vitest";
import {
  buildPayload,
  deriveDesired,
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
