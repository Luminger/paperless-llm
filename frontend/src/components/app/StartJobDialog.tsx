// Scheduling a job is a decision, not a reflex: the dashboard's
// one-click starters open THIS modal instead — scope is preset, the
// options (auto-apply, instructions, extra knobs) are in plain sight,
// and nothing runs until Start.

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ErrorNotice } from "@/components/app/states";

export function StartJobDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  busy = false,
  error,
  onStart,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  /** Extra scope knobs (e.g. batch size), rendered above the options. */
  children?: React.ReactNode;
  busy?: boolean;
  error?: unknown;
  onStart: (opts: { apply_policy: "review" | "auto"; instructions?: string }) => void;
}) {
  const [auto, setAuto] = useState(false);
  const [instructions, setInstructions] = useState("");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {children}
          <Label className="flex items-center gap-1.5 text-sm font-normal">
            <Checkbox checked={auto} onCheckedChange={(v) => setAuto(v === true)} />
            auto-apply proposals (journaled &amp; revertible)
          </Label>
          <Textarea
            aria-label="job instructions"
            rows={2}
            placeholder="Optional instructions for the agent…"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
          <ErrorNotice error={error} />
        </div>
        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={busy}
            onClick={() =>
              onStart({
                apply_policy: auto ? "auto" : "review",
                instructions: instructions.trim() || undefined,
              })
            }
          >
            Start job
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
