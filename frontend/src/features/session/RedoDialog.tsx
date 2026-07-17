// A redo always asks HOW it should run differently — and warns that it
// supersedes the step and everything after it.

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ErrorNotice } from "@/components/app/states";
import type { Step } from "../../api";

// Which input fields a redo may amend, per step kind.
const REDO_FIELDS: Record<Step["kind"], { key: string; label: string; long?: boolean }[]> = {
  ocr: [
    { key: "instructions", label: "OCR instructions" },
    { key: "dpi", label: "render DPI" },
  ],
  analysis: [{ key: "instructions", label: "instructions for the agent" }],
  chat: [{ key: "content", label: "message", long: true }],
};

export function RedoDialog({
  step,
  open,
  onOpenChange,
  onConfirm,
  busy,
  error,
}: {
  step: Step;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (input: Record<string, unknown>) => void;
  busy: boolean;
  error: unknown;
}) {
  const fields = REDO_FIELDS[step.kind];
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      fields.map((f) => [f.key, step.input[f.key] != null ? String(step.input[f.key]) : ""]),
    ),
  );
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Redo this step</DialogTitle>
          <DialogDescription>
            Redoing supersedes this step <strong>and every step after it</strong> —
            later results (including open proposals) were built on it. Superseded
            steps stay inspectable on the timeline.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {fields.map((f) =>
            f.long ? (
              <div key={f.key} className="space-y-1">
                <Label htmlFor={`redo-${f.key}`}>{f.label}</Label>
                <Textarea
                  id={`redo-${f.key}`}
                  aria-label={`redo ${f.label}`}
                  rows={2}
                  value={values[f.key]}
                  onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                />
              </div>
            ) : (
              <div key={f.key} className="space-y-1">
                <Label htmlFor={`redo-${f.key}`}>{f.label}</Label>
                <Input
                  id={`redo-${f.key}`}
                  aria-label={`redo ${f.label}`}
                  value={values[f.key]}
                  onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                />
              </div>
            ),
          )}
          <ErrorNotice error={error} />
        </div>
        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={busy}
            onClick={() => {
              const input: Record<string, unknown> = {};
              for (const f of fields) {
                const raw = values[f.key].trim();
                if (raw === "") continue;
                input[f.key] = f.key === "dpi" ? Number(raw) : raw;
              }
              onConfirm(input);
            }}
          >
            Redo step
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
