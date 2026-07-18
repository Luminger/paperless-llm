// List/filter/pagination state belongs in the URL: deep links work,
// refresh keeps the view, back/forward navigates filter history.
// One tiny hook so every page does it the same way.

import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

export function useUrlParam(
  key: string,
  fallback = "",
): [string, (v: string) => void] {
  const [params, setParams] = useSearchParams();
  const value = params.get(key) ?? fallback;
  const set = useCallback(
    (v: string) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (!v || v === fallback) next.delete(key);
          else next.set(key, v);
          return next;
        },
        { replace: true },
      );
    },
    [key, fallback, setParams],
  );
  return [value, set];
}

/** Numeric variant (pages, entity-id filters). 0/NaN clears the param. */
export function useUrlNumber(
  key: string,
  fallback = 0,
): [number, (v: number) => void] {
  const [raw, setRaw] = useUrlParam(key, String(fallback));
  const value = Number(raw) || fallback;
  const set = useCallback(
    (v: number) => setRaw(v && v !== fallback ? String(v) : ""),
    [setRaw, fallback],
  );
  return [value, set];
}

/** Batched update for handlers that touch several params at once
 * (filter change + page reset). Two synchronous set calls would lose
 * the first one — navigation only happens after the handler returns. */
export function useUrlPatch() {
  const [, setParams] = useSearchParams();
  return useCallback(
    (patch: Record<string, string | number | null | undefined>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            const str = v == null ? "" : String(v);
            if (!str || str === "0" || (k === "page" && str === "1")) next.delete(k);
            else next.set(k, str);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );
}
