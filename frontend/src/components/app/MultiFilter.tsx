// THE multiselect filter: a dropdown of checkboxes over taxonomy
// entities. Reads like SimpleSelect when nothing is picked ("any
// tag"), names a single pick, counts beyond that ("2 tags"). Values
// live in the URL as comma-separated ids next to every other filter.

import { ChevronDown } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

export function MultiFilter({
  label,
  plural,
  options,
  values,
  onChange,
  className,
}: {
  /** Noun for the empty state and aria label ("tag"). */
  label: string;
  /** Plural noun for counts; defaults to `${label}s`. */
  plural?: string;
  options: { id: number; name: string }[] | undefined;
  values: number[];
  onChange: (v: number[]) => void;
  className?: string;
}) {
  const chosen = new Set(values);
  const text =
    values.length === 0
      ? `any ${label}`
      : values.length === 1
        ? (options?.find((o) => o.id === values[0])?.name ?? "…")
        : `${values.length} ${plural ?? `${label}s`}`;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={`filter by ${label}`}
        className={cn(
          "flex h-8 items-center gap-2 rounded-md border border-input bg-transparent px-3 py-2 text-sm whitespace-nowrap shadow-xs transition-[color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
          values.length > 0 ? "text-foreground" : "text-muted-foreground",
          className,
        )}
      >
        {text}
        <ChevronDown className="size-4 opacity-50" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 w-56 overflow-y-auto">
        {(options ?? []).map((o) => (
          <DropdownMenuCheckboxItem
            key={o.id}
            checked={chosen.has(o.id)}
            // Keep the menu open: filters are usually refined in bursts.
            onSelect={(e) => e.preventDefault()}
            onCheckedChange={(checked) =>
              onChange(
                checked
                  ? [...values, o.id]
                  : values.filter((v) => v !== o.id),
              )
            }
          >
            {o.name}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
