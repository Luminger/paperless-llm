import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ErrorNotice } from "@/components/app/states";
import { api } from "../../api";
import { keys } from "../../lib/keys";

/** Prompt tuning: the base prompts are system-supplied but overridable
 * (small local models often need it); additions append user context
 * (archive purpose, house rules). Persisted server-side. */
function PromptField({
  label,
  hint,
  value,
  placeholder,
  onChange,
  rows = 4,
}: {
  label: string;
  hint?: string;
  value: string;
  placeholder?: string;
  onChange: (v: string) => void;
  rows?: number;
}) {
  return (
    <div className="space-y-1">
      <Label className="font-normal text-muted-foreground">{label}</Label>
      {hint && <p className="text-xs text-muted-foreground/60">{hint}</p>}
      <Textarea
        aria-label={label.toLowerCase()}
        rows={rows}
        className="font-mono text-xs"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

export function PromptTuning({
  defaults,
}: {
  defaults: { agent_base: string; ocr_base: string };
}) {
  const { data: prefs } = useQuery({ queryKey: keys.prefs(), queryFn: api.getPrefs });
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  const save = useMutation({
    mutationFn: (body: Record<string, string>) => api.putPrefs(body),
  });
  if (!prefs) return null;
  const cur = draft ?? {
    agent_prompt_addition: prefs.agent_prompt_addition,
    agent_prompt_base: prefs.agent_prompt_base,
    ocr_prompt_addition: prefs.ocr_prompt_addition,
    ocr_prompt_base: prefs.ocr_prompt_base,
  };
  const set = (k: string) => (v: string) => setDraft({ ...cur, [k]: v });
  const dirty = draft != null;
  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle className="text-base">Prompts</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <PromptField
          label="Additional agent instructions"
          hint="Appended to every agent prompt — describe your archive, its purpose, house rules (e.g. language of titles, who the archive owner is)."
          value={cur.agent_prompt_addition}
          placeholder="e.g. The archive belongs to Simon; correspondents are always the OTHER party. Titles in the document's language."
          onChange={set("agent_prompt_addition")}
        />
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground select-none">
            Advanced: override the agent base prompt (leave empty for the system default)
          </summary>
          <div className="mt-2">
            <PromptField
              label="Agent base prompt override"
              value={cur.agent_prompt_base}
              placeholder={defaults.agent_base}
              onChange={set("agent_prompt_base")}
              rows={10}
            />
          </div>
        </details>
        <PromptField
          label="Additional OCR instructions"
          hint="Appended to the OCR prompt for every transcription."
          value={cur.ocr_prompt_addition}
          placeholder="e.g. Stamps and handwritten margin notes matter — transcribe them."
          onChange={set("ocr_prompt_addition")}
        />
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground select-none">
            Advanced: override the OCR base prompt (leave empty for the system default)
          </summary>
          <div className="mt-2">
            <PromptField
              label="OCR base prompt override"
              value={cur.ocr_prompt_base}
              placeholder={defaults.ocr_base}
              onChange={set("ocr_prompt_base")}
              rows={8}
            />
          </div>
        </details>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate(cur, { onSuccess: () => setDraft(null) })}
          >
            Save prompts
          </Button>
          {save.isSuccess && !dirty && <span className="text-xs text-primary">saved</span>}
          <ErrorNotice error={save.error} />
        </div>
      </CardContent>
    </Card>
  );
}

