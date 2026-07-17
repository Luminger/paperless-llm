import { useState } from "react";
import { Button } from "@/components/ui/button";

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
    <div className="flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 p-2 text-sm">
      <span className="font-medium">{count} selected</span>
      <Button size="sm" disabled={count === 0 || busy} onClick={onAction}>
        {actionLabel}
      </Button>
      <span className="flex-1" />
      <Button variant="ghost" size="sm" onClick={() => onSelectAll(allIds)}>
        Select all
      </Button>
      <Button variant="ghost" size="sm" onClick={onUnselectAll}>
        Unselect all
      </Button>
      <Button variant="ghost" size="sm" onClick={onCancel}>
        Cancel
      </Button>
    </div>
  );
}
