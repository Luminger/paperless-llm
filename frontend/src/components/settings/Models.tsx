// Model & behavior configuration — the RUNTIME-EDITABLE slice of the
// config. Precedence is environment > here > config file > defaults:
// env-locked keys render disabled with a lock, everything else is
// editable by admins and stored server-side immediately on save.

import { useState } from "react";
import { Tip } from "@/components/app/Tip";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SimpleSelect } from "@/components/app/SimpleSelect";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api, type ConfigRow } from "../../api";
import { keys } from "../../lib/keys";
import { useAuth } from "../../lib/auth";
import { SourceBadge } from "./shared";

const GROUPS: { title: string; prefix: string[]; hint?: string }[] = [
  { title: "Agent model", prefix: ["llm.agent."] },
  {
    title: "OCR model",
    prefix: ["llm.ocr."],
    hint: "Unset endpoint/model fall back to the agent profile.",
  },
  { title: "Embeddings", prefix: ["llm.embeddings."] },
  { title: "Reranker", prefix: ["llm.reranker."] },
  {
    title: "Behavior",
    prefix: ["queue.", "webhook."],
    hint: "Webhook defaults apply to documents arriving via the paperless workflow.",
  },
];

const LABELS: Record<string, string> = {
  base_url: "Endpoint",
  model: "Model",
  api_key: "API key",
  max_concurrent: "Max concurrent requests",
  supports_streaming: "Token streaming",
  thinking: "Thinking mode",
  max_input_tokens: "Input token clamp",
  max_tool_iterations: "Tool iterations per turn",
  max_images_per_request: "Images per request",
  max_pages: "Page cap (0 = all)",
  render_dpi: "Render DPI",
  auto_continuation_limit: "Auto-continuation limit",
  redo_ocr: "Webhook: re-do OCR",
  apply_policy: "Webhook: apply policy",
};

const CHOICES: Record<string, { value: string; label: string }[]> = {
  thinking: ["server_default", "on", "off"].map((v) => ({ value: v, label: v })),
  apply_policy: [
    { value: "review", label: "review" },
    { value: "auto", label: "auto (journaled)" },
  ],
};

function FieldEditor({
  row,
  draft,
  onChange,
  disabled,
}: {
  row: ConfigRow;
  draft: unknown;
  onChange: (v: unknown) => void;
  disabled: boolean;
}) {
  const field = row.key.split(".").pop()!;
  const value = draft !== undefined ? draft : row.value;
  if (typeof row.value === "boolean" || field === "supports_streaming" || field === "redo_ocr") {
    return (
      <SimpleSelect
        ariaLabel={row.key}
        disabled={disabled}
        value={String(value ?? false)}
        onValueChange={(v) => onChange(v === "true")}
        options={[
          { value: "true", label: "on" },
          { value: "false", label: "off" },
        ]}
      />
    );
  }
  if (CHOICES[field]) {
    return (
      <SimpleSelect
        ariaLabel={row.key}
        disabled={disabled}
        value={String(value ?? "")}
        onValueChange={onChange}
        options={CHOICES[field]}
      />
    );
  }
  if (row.secret) {
    return (
      <Input
        aria-label={row.key}
        type="password"
        disabled={disabled}
        placeholder={row.is_set ? "•••••• (set — type to replace)" : "not set"}
        value={typeof draft === "string" ? draft : ""}
        onChange={(e) => onChange(e.target.value)}
        className="h-8"
      />
    );
  }
  const numeric = typeof row.value === "number";
  return (
    <Input
      aria-label={row.key}
      type={numeric ? "number" : "text"}
      disabled={disabled}
      value={String(value ?? "")}
      onChange={(e) => onChange(numeric ? Number(e.target.value) : e.target.value)}
      className="h-8"
    />
  );
}

export function ModelsConfig() {
  const { role } = useAuth();
  const qc = useQueryClient();
  const isAdmin = role === "admin";
  const { data: rows, error, isLoading } = useQuery({
    queryKey: keys.config(),
    queryFn: api.getConfig,
  });
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const save = useMutation({
    mutationFn: () => api.putConfig(draft),
    onSuccess: (fresh) => {
      qc.setQueryData(keys.config(), fresh);
      qc.invalidateQueries({ queryKey: keys.settings() });
      setDraft({});
    },
  });
  if (error) return <ErrorNotice error={error} />;
  if (isLoading || !rows) return <LoadingState lines={6} />;
  const dirty = Object.keys(draft).length > 0;

  return (
    <div className="grid gap-4">
      {!isAdmin && (
        <p className="rounded-lg border bg-muted/40 p-3 text-sm text-muted-foreground">
          Changing these values requires administrator rights (a paperless
          superuser account).
        </p>
      )}
      {GROUPS.map((group) => {
        const groupRows = rows.filter((r) =>
          group.prefix.some((p) => r.key.startsWith(p)),
        );
        if (groupRows.length === 0) return null;
        return (
          <Card key={group.title}>
            <CardHeader>
              <CardTitle className="text-base">{group.title}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {group.hint && (
                <p className="mb-2 text-xs text-muted-foreground/70">{group.hint}</p>
              )}
              {groupRows.map((row) => (
                <div
                  key={row.key}
                  className="grid grid-cols-[13rem_1fr_auto] items-center gap-3"
                >
                  <Label className="font-normal text-muted-foreground">
                    {LABELS[row.key.split(".").pop()!] ?? row.key}
                  </Label>
                  <FieldEditor
                    row={row}
                    draft={draft[row.key]}
                    onChange={(v) => setDraft((d) => ({ ...d, [row.key]: v }))}
                    disabled={!isAdmin || !row.editable}
                  />
                  <span className="flex w-28 justify-end gap-1">
                    {row.source === "ui" && isAdmin && draft[row.key] === undefined ? (
                      <Tip content="Remove this override (falls back to config file / default)">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-6 px-2 text-xs text-muted-foreground"
                          onClick={() => setDraft((d) => ({ ...d, [row.key]: null }))}
                        >
                          reset
                        </Button>
                      </Tip>
                    ) : (
                      <SourceBadge source={row.source} />
                    )}
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
        );
      })}
      {isAdmin && (
        <div className="sticky bottom-0 flex items-center gap-3 border-t bg-background py-3">
          <Button size="sm" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
            Save changes
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={!dirty}
            onClick={() => setDraft({})}
          >
            Discard
          </Button>
          <span className="text-xs text-muted-foreground">
            Changes apply immediately — no restart needed.
          </span>
          <ErrorNotice error={save.error} />
        </div>
      )}
    </div>
  );
}
