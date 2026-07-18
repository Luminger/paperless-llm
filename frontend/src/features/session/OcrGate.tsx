// The OCR review gate: editable diff, accept/keep decisions, and the
// gate's own steering affordance (re-run with instructions).

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api, type Step } from "../../api";
import { keys } from "../../lib/keys";
import { DiffView } from "../../components/DiffView";

export function OcrGateBody({ step }: { step: Step }) {
  const qc = useQueryClient();
  const { data: ocr } = useQuery({
    queryKey: keys.sessionOcr(step.session_id, step.id),
    queryFn: () => api.getOcrReview(step.session_id),
  });
  const [newText, setNewText] = useState<string | null>(null);
  const [rerunInstructions, setRerunInstructions] = useState("");
  const invalidate = () =>
    qc.invalidateQueries({ queryKey: keys.session(step.session_id) });
  const resolve = useMutation({
    mutationFn: (content: string | null) =>
      api.resolveStep(step.session_id, step.id, content),
    onSuccess: invalidate,
  });
  const redo = useMutation({
    mutationFn: () =>
      api.redoStep(step.session_id, step.id, {
        instructions: rerunInstructions.trim() || undefined,
      }),
    onSuccess: invalidate,
  });

  useEffect(() => {
    if (ocr && newText === null) setNewText(ocr.ocr_text);
  }, [ocr, newText]);

  if (!ocr || newText === null) return <LoadingState lines={3} />;

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Review the re-OCRed content. You can fix mistakes directly in the new text
        before accepting. The analysis continues only after this step — based on
        whatever content you decide on.
      </p>
      <DiffView oldText={ocr.previous_content} newText={newText} onNewTextChange={setNewText} />
      <div className="flex gap-2">
        <Button
          size="sm"
          onClick={() => resolve.mutate(newText)}
          disabled={resolve.isPending || redo.isPending}
        >
          Accept {newText !== ocr.ocr_text ? "(with your fixes) " : ""}&amp; continue
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => resolve.mutate(null)}
          disabled={resolve.isPending || redo.isPending}
        >
          Keep existing content &amp; continue
        </Button>
      </div>
      <details className="rounded-lg border bg-muted/40 p-2">
        <summary className="cursor-pointer text-sm text-muted-foreground select-none">
          Not happy with the OCR? Re-run it with instructions
        </summary>
        <div className="mt-2 space-y-2">
          <Textarea
            aria-label="re-run instructions"
            rows={2}
            placeholder="e.g. transcribe the handwritten stamp in the corner too"
            value={rerunInstructions}
            onChange={(e) => setRerunInstructions(e.target.value)}
          />
          <Button
            size="sm"
            variant="secondary"
            onClick={() => redo.mutate()}
            disabled={redo.isPending || resolve.isPending}
          >
            Re-run OCR
          </Button>
        </div>
      </details>
      <ErrorNotice error={resolve.error} />
      <ErrorNotice error={redo.error} />
    </div>
  );
}
