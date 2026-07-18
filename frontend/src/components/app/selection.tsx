// THE list-selection pattern (gmail style): checkboxes are always
// visible, a toolbar appears once something is selected. No separate
// "selection mode" to toggle on and off.

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";

/** ``scopeKey``: identity of the list the selection belongs to. When it
 * changes (e.g. /taxonomy/tag -> /taxonomy/correspondent renders the
 * same component), the selection self-clears — numeric ids overlap
 * across entity tables, so a stale selection would silently check the
 * WRONG rows (AUDIT FP-H1). */
export function useSelection(scopeKey?: string) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const prevScope = useRef(scopeKey);
  if (prevScope.current !== scopeKey) {
    // Render-time state adjustment (the React-sanctioned pattern):
    // the render restarts with an empty selection before commit.
    prevScope.current = scopeKey;
    setSelected(new Set());
  }
  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const add = (ids: number[]) =>
    setSelected((prev) => new Set([...prev, ...ids]));
  const remove = (ids: number[]) =>
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of ids) next.delete(id);
      return next;
    });
  const clear = () => setSelected(new Set());
  return { selected, toggle, add, remove, clear };
}

/** Header checkbox: selects/unselects everything VISIBLE (one page);
 * indeterminate while only part of it is selected. */
export function SelectAllHeader({
  ids,
  selection,
  label = "select all on this page",
}: {
  ids: number[];
  selection: ReturnType<typeof useSelection>;
  label?: string;
}) {
  const picked = ids.filter((id) => selection.selected.has(id)).length;
  const state =
    picked === 0 ? false : picked === ids.length ? true : "indeterminate";
  return (
    <Checkbox
      aria-label={label}
      checked={state}
      onCheckedChange={(v) => (v ? selection.add(ids) : selection.remove(ids))}
    />
  );
}

/** Toolbar shown while a selection exists: count, the bulk action,
 * select-ALL-matching (across pages), clear. */
export function SelectionBar({
  selection,
  allIds,
  actionLabel,
  busy = false,
  onAction,
}: {
  selection: ReturnType<typeof useSelection>;
  /** Every matching id across ALL pages (cross-page select-all). */
  allIds: number[];
  actionLabel: string;
  busy?: boolean;
  onAction: () => void;
}) {
  const count = selection.selected.size;
  if (count === 0) return null;
  return (
    <div className="mb-3 flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 p-2 text-sm">
      <span className="font-medium">{count} selected</span>
      <Button size="sm" disabled={busy} onClick={onAction}>
        {actionLabel}
      </Button>
      <span className="flex-1" />
      {count < allIds.length && (
        <Button variant="ghost" size="sm" onClick={() => selection.add(allIds)}>
          Select all {allIds.length}
        </Button>
      )}
      <Button variant="ghost" size="sm" onClick={selection.clear}>
        Clear
      </Button>
    </div>
  );
}
