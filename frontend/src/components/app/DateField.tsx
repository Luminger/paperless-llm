// The one date picker: framework calendar in a popover; the trigger
// renders the value in the user's chosen date format & timezone-free
// (paperless dates are plain dates, not instants).

import { useState } from "react";
import { CalendarIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { formatDate } from "../../lib/format";

const pad = (n: number) => String(n).padStart(2, "0");

function toYmd(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function fromYmd(v: string | null): Date | undefined {
  if (!v) return undefined;
  const [y, m, d] = v.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return undefined;
  return new Date(y, m - 1, d);
}

export function DateField({
  value,
  onChange,
  disabled,
  ariaLabel,
  className,
}: {
  value: string | null;
  onChange: (v: string | null) => void;
  disabled?: boolean;
  ariaLabel: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          aria-label={ariaLabel}
          disabled={disabled}
          className={cn(
            "h-8 justify-start gap-2 font-normal",
            !value && "text-muted-foreground",
            className,
          )}
        >
          <CalendarIcon className="size-3.5" />
          {value ? formatDate(value) : "pick a date"}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="single"
          selected={fromYmd(value)}
          defaultMonth={fromYmd(value)}
          onSelect={(d) => {
            onChange(d ? toYmd(d) : null);
            setOpen(false);
          }}
        />
      </PopoverContent>
    </Popover>
  );
}
