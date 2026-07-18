// THE list-page controller (AUDIT FP-C1): every top-level list wires
// page/size from the URL the same way, and out-of-range pages clamp
// back to the last real page instead of showing a misleading
// "no results" empty state (FP-L2).

import { useEffect } from "react";
import { useUrlNumber, useUrlPatch } from "./useUrlState";

export function useListPage(defaultSize = 25) {
  const [page, setPage] = useUrlNumber("page", 1);
  const [pageSize] = useUrlNumber("size", defaultSize);
  const patch = useUrlPatch();
  const setPageSize = (n: number) => patch({ size: n === defaultSize ? null : n, page: null });
  return { page, setPage, pageSize, setPageSize, patch };
}

/** Deep links / shrunk data: `?page=40` of a 3-page result set snaps to
 * the last page (an effect, so the URL stays honest). */
export function useClampPage(
  page: number,
  setPage: (n: number) => void,
  data: { count: number; results: unknown[] } | undefined,
  pageSize: number,
  /** Proxied lists (paperless) answer out-of-range pages with a 404
   * ERROR rather than an empty page — treat that as out-of-range too. */
  error?: unknown,
) {
  useEffect(() => {
    if (page <= 1) return;
    if (error != null && data == null) {
      setPage(1); // can't know the last page — restart at the front
      return;
    }
    if (!data || data.results.length > 0 || data.count === 0) return;
    setPage(Math.max(1, Math.ceil(data.count / pageSize)));
  }, [data, error, page, pageSize, setPage]);
}
