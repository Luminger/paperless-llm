// The one place taxonomy lists are fetched and ids become names.
// Three components used to hand-roll the same four queries and three
// copies of the "(unknown)" / "…" fallback convention.

import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { keys } from "../lib/keys";
import type { components } from "../api/schema.gen";

export type EntityRef = components["schemas"]["EntityOut"];

const FETCHERS = {
  tag: api.listTags,
  correspondent: api.listCorrespondents,
  document_type: api.listDocumentTypes,
  storage_path: api.listStoragePaths,
} as const;

export type TaxonomyType = keyof typeof FETCHERS;

export function isTaxonomyType(t: string): t is TaxonomyType {
  return t in FETCHERS;
}

export function useEntityList(type: TaxonomyType) {
  return useQuery({ queryKey: keys.entities(type), queryFn: FETCHERS[type] });
}

/** All four taxonomy lists at once (proposal editors, fact sheets). */
export function useTaxonomyLists() {
  const tags = useEntityList("tag");
  const correspondents = useEntityList("correspondent");
  const docTypes = useEntityList("document_type");
  const storagePaths = useEntityList("storage_path");
  return {
    tags: tags.data,
    correspondents: correspondents.data,
    docTypes: docTypes.data,
    storagePaths: storagePaths.data,
  };
}

/** THE fallback convention: name, "(unknown)" once loaded, "…" while
 * loading. Raw ids never surface. */
export function entityName(
  list: { id: number; name: string }[] | undefined,
  id: number | null | undefined,
): string {
  if (id == null) return "";
  return list?.find((e) => e.id === id)?.name ?? (list ? "(unknown)" : "…");
}
