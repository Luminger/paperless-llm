// Scheduling a job is a decision, not a reflex: the dashboard's
// one-click starters open THIS modal instead — scope is preset, the
// options (auto-apply, instructions, extra knobs) are in plain sight,
// and nothing runs until Start.

import { useState, useEffect } from "react";
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
  redoOcrOption = false,
  autoLabel = "auto-apply proposals (journaled & revertible)",
  startLabel = "Start job",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  /** Extra scope knobs (e.g. batch size), rendered above the options. */
  children?: React.ReactNode;
  busy?: boolean;
  error?: unknown;
  onStart: (opts: {
    apply_policy: "review" | "auto";
    instructions?: string;
    redo_ocr?: boolean;
  }) => void;
  /** Offer a "re-do OCR first" checkbox (document-scoped analyze jobs). */
  redoOcrOption?: boolean;
  /** The auto-apply checkbox text — OCR-only jobs apply text, not proposals. */
  autoLabel?: string;
  startLabel?: string;
}) {
  const [auto, setAuto] = useState(false);
  const [redoOcr, setRedoOcr] = useState(false);
  const [instructions, setInstructions] = useState("");
  // Fresh state per open (AUDIT FP-L9): dashboard callers keep this
  // mounted, so stale auto/instructions/error must not leak into the
  // next opening.
  useEffect(() => {
    if (open) {
      setAuto(false);
      setRedoOcr(false);
      setInstructions("");
    }
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {children}
          {redoOcrOption && (
            <Label className="flex items-center gap-1.5 text-sm font-normal">
              <Checkbox
                checked={redoOcr}
                onCheckedChange={(v) => setRedoOcr(v === true)}
              />
              re-do OCR first (vision model; the new text passes the OCR gate)
            </Label>
          )}
          <Label className="flex items-center gap-1.5 text-sm font-normal">
            <Checkbox checked={auto} onCheckedChange={(v) => setAuto(v === true)} />
            {autoLabel}
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
                ...(redoOcrOption ? { redo_ocr: redoOcr || undefined } : {}),
              })
            }
          >
            {startLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
