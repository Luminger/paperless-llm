import { useState } from "react";

/** Generic cross-page multiselect state for entity lists. */
export function useMultiSelect() {
  const [active, setActive] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const selectAll = (ids: number[]) =>
    setSelected((prev) => new Set([...prev, ...ids]));
  const unselectAll = () => setSelected(new Set());
  const cancel = () => {
    setActive(false);
    setSelected(new Set());
  };
  return { active, setActive, selected, toggle, selectAll, unselectAll, cancel };
}

/** Toolbar for an active multiselect: count, bulk action, select-all /
 * unselect-all / cancel. `allIds` covers the WHOLE list (all pages). */
export function MultiSelectBar({
  count,
  allIds,
  actionLabel,
  busy,
  onAction,
  onSelectAll,
  onUnselectAll,
  onCancel,
}: {
  count: number;
  allIds: number[];
  actionLabel: string;
  busy: boolean;
  onAction: () => void;
  onSelectAll: (ids: number[]) => void;
  onUnselectAll: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="flex items-center gap-2 rounded border border-emerald-200 bg-emerald-50/60 p-2 text-sm">
      <span className="font-medium text-emerald-900">{count} selected</span>
      <button
        className="rounded bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-700 disabled:opacity-50"
        disabled={count === 0 || busy}
        onClick={onAction}
      >
        {actionLabel}
      </button>
      <span className="flex-1" />
      <button
        className="rounded bg-zinc-100 px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-200"
        onClick={() => onSelectAll(allIds)}
      >
        Select all
      </button>
      <button
        className="rounded bg-zinc-100 px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-200"
        onClick={onUnselectAll}
      >
        Unselect all
      </button>
      <button
        className="rounded bg-zinc-100 px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-200"
        onClick={onCancel}
      >
        Cancel
      </button>
    </div>
  );
}
