// The one way back to an unfiltered list: appears next to the filters
// whenever any of them is active (the selection bar's "Clear" sibling).

import { X } from "lucide-react";
import { Button } from "@/components/ui/button";

export function ResetFilters({
  active,
  onReset,
}: {
  active: boolean;
  onReset: () => void;
}) {
  if (!active) return null;
  return (
    <Button
      variant="ghost"
      size="sm"
      className="h-8 text-muted-foreground"
      onClick={onReset}
    >
      <X className="size-3.5" />
      Reset filters
    </Button>
  );
}
