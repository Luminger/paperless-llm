// THE custom-field vocabulary: how a paperless data_type is named for
// users and how a value displays. The proposal editor, document facts
// and the taxonomy registry page all speak it.

import type { CustomFieldDef } from "../api";
import { formatDate } from "./format";

/** Human name per paperless custom-field data_type. */
export const CUSTOM_FIELD_TYPE_LABELS: Record<string, string> = {
  string: "Text",
  url: "URL",
  date: "Date",
  boolean: "Yes / no",
  integer: "Whole number",
  float: "Number",
  monetary: "Money",
  select: "Choice",
  documentlink: "Document links",
};

export const customFieldTypeLabel = (dt: string): string =>
  CUSTOM_FIELD_TYPE_LABELS[dt] ?? dt;

/** Typed display of a custom-field value — never the raw JSON. */
export function displayCustomValue(
  def: CustomFieldDef | undefined,
  value: unknown,
): string {
  if (value == null || value === "") return "—";
  switch (def?.data_type) {
    case "boolean":
      return value ? "yes" : "no";
    case "date":
      return formatDate(String(value));
    case "select": {
      const opt = (def.select_options ?? []).find((o) => o.id === value);
      return String(opt?.label ?? value);
    }
    case "documentlink": {
      const n = Array.isArray(value) ? value.length : 0;
      return `${n} document${n === 1 ? "" : "s"}`;
    }
    default:
      return String(value);
  }
}
