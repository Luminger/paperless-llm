// THE guard modal: any destructive or irreversible-feeling action asks
// once, in the same voice, before doing anything.
//
// Built on AlertDialog (AUDIT UI-U3): role="alertdialog", focus starts
// on "Keep it", and there is NO outside-click/Esc-accident path to
// confirming — the user must choose. Failures render inline (FP-M3):
// a guard modal that fails silently defeats its own purpose.

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { ErrorNotice } from "./states";

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  busy = false,
  error = null,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  confirmLabel?: string;
  busy?: boolean;
  /** Mutation error — rendered inline so a failed action is never silent. */
  error?: unknown;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="sm:max-w-md">
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        {error != null && <ErrorNotice error={error} />}
        <AlertDialogFooter>
          <Button
            variant="ghost"
            size="sm"
            autoFocus
            onClick={() => onOpenChange(false)}
          >
            Keep it
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={busy}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
