// Prompt tuning, in the open: every prompt is a full section showing
// the ACTUAL effective text (system default or the user's override),
// each with its own revert-to-default. Empty override on the server
// means "use the system default", so default improvements keep
// flowing until the user really forks the prompt.

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ErrorNotice } from "@/components/app/states";
import { api } from "../../api";
import { keys } from "../../lib/keys";

function PromptSection({
  label,
  hint,
  value,
  defaultValue,
  placeholder,
  rows,
  onChange,
}: {
  label: string;
  hint: string;
  /** The effective text shown in the editor. */
  value: string;
  /** System default; undefined = free-text field (additions have no
   * default to revert to — their default is simply blank). */
  defaultValue?: string;
  placeholder?: string;
  rows: number;
  onChange: (v: string) => void;
}) {
  const modified =
    defaultValue !== undefined && value.trim() !== defaultValue.trim();
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <Label className="font-medium">{label}</Label>
        {modified && (
          <Badge variant="secondary" className="text-amber-700 dark:text-amber-300">
            modified
          </Badge>
        )}
        <span className="flex-1" />
        {defaultValue !== undefined && (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 gap-1 px-2 text-xs text-muted-foreground"
            disabled={!modified}
            onClick={() => onChange(defaultValue)}
          >
            <RotateCcw className="size-3" /> Revert to default
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground/70">{hint}</p>
      <Textarea
        aria-label={label.toLowerCase()}
        rows={rows}
        className="font-mono text-xs leading-5"
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

  // The editor always shows the EFFECTIVE prompt text.
  const cur = draft ?? {
    agent_prompt_base: prefs.agent_prompt_base.trim() || defaults.agent_base,
    agent_prompt_addition: prefs.agent_prompt_addition,
    ocr_prompt_base: prefs.ocr_prompt_base.trim() || defaults.ocr_base,
    ocr_prompt_addition: prefs.ocr_prompt_addition,
  };
  const set = (k: string) => (v: string) => setDraft({ ...cur, [k]: v });
  const dirty = draft != null;

  const persist = () =>
    save.mutate(
      {
        // Matching the default is stored as "" — stays on the default.
        agent_prompt_base:
          cur.agent_prompt_base.trim() === defaults.agent_base.trim()
            ? ""
            : cur.agent_prompt_base,
        ocr_prompt_base:
          cur.ocr_prompt_base.trim() === defaults.ocr_base.trim()
            ? ""
            : cur.ocr_prompt_base,
        agent_prompt_addition: cur.agent_prompt_addition,
        ocr_prompt_addition: cur.ocr_prompt_addition,
      },
      { onSuccess: () => setDraft(null) },
    );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Prompts</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <PromptSection
          label="Additional agent instructions"
          hint="The place for YOUR context — appended to every agent prompt. Describe the archive, its purpose, house rules (e.g. language of titles, who the archive owner is)."
          value={cur.agent_prompt_addition}
          placeholder="e.g. The archive belongs to Simon; correspondents are always the OTHER party."
          rows={4}
          onChange={set("agent_prompt_addition")}
        />
        <PromptSection
          label="Agent system prompt"
          hint="The base prompt every agent runs with. System-supplied and usually best left alone — edit only to tune for your model; a modified prompt no longer receives system updates."
          value={cur.agent_prompt_base}
          defaultValue={defaults.agent_base}
          rows={14}
          onChange={set("agent_prompt_base")}
        />
        <PromptSection
          label="Additional OCR instructions"
          hint="The place for YOUR transcription rules — appended to the OCR prompt."
          value={cur.ocr_prompt_addition}
          placeholder="e.g. Stamps and handwritten margin notes matter — transcribe them."
          rows={3}
          onChange={set("ocr_prompt_addition")}
        />
        <PromptSection
          label="OCR system prompt"
          hint="The base prompt for every transcription. System-supplied and usually best left alone — edit only to tune for your OCR model."
          value={cur.ocr_prompt_base}
          defaultValue={defaults.ocr_base}
          rows={10}
          onChange={set("ocr_prompt_base")}
        />
        <div className="flex items-center gap-2">
          <Button size="sm" disabled={!dirty || save.isPending} onClick={persist}>
            Save prompts
          </Button>
          {save.isSuccess && !dirty && <span className="text-xs text-primary">saved</span>}
          <ErrorNotice error={save.error} />
        </div>
      </CardContent>
    </Card>
  );
}
