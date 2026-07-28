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
import { api, type ConfigRow, type LlmDetect, type LlmTest } from "../../api";
import { keys } from "../../lib/keys";
import { useAuth } from "../../lib/auth";
import { SourceBadge } from "./shared";

type ProbeProfile = "agent" | "ocr" | "embeddings" | "reranker";

const GROUPS: {
  title: string;
  prefix: string[];
  hint?: string;
  probe?: ProbeProfile;
}[] = [
  { title: "Agent model", prefix: ["llm.agent."], probe: "agent" },
  {
    title: "OCR model",
    prefix: ["llm.ocr."],
    hint: "Unset endpoint/model fall back to the agent profile.",
    probe: "ocr",
  },
  { title: "Embeddings", prefix: ["llm.embeddings."], probe: "embeddings" },
  { title: "Reranker", prefix: ["llm.reranker."], probe: "reranker" },
  {
    title: "Behavior",
    prefix: ["queue."],
  },
];
// Webhook keys live on the Paperless tab (Webhook.tsx) — next to the
// status they belong to.

export const LABELS: Record<string, string> = {
  base_url: "Endpoint",
  model: "Model",
  api_key: "API key",
  max_concurrent: "Max concurrent requests",
  timeout_seconds: "Max call time (seconds)",
  supports_streaming: "Token streaming",
  thinking: "Thinking mode",
  max_input_tokens: "Input token clamp",
  max_tool_iterations: "Tool iterations per turn",
  max_images_per_request: "Images per request",
  max_pages: "Page cap (0 = all)",
  render_dpi: "Render DPI",
  native_text: "Born-digital gate (read embedded text)",
  native_auto_accept_similarity: "Born-digital auto-accept similarity",
  temperature: "Temperature",
  top_p: "Top-p",
  top_k: "Top-k",
  min_p: "Min-p",
  max_tokens: "Output token cap per call",
  presence_penalty: "Presence penalty",
  frequency_penalty: "Frequency penalty",
  repetition_penalty: "Repetition penalty",
  auto_continuation_limit: "Auto-continuation limit",
  secret: "Shared secret",
  public_url: "This app's URL (as paperless sees it)",
  redo_ocr: "Re-do OCR on arrival",
  apply_policy: "Apply policy",
};

// Per-key explanations (hover on the label). The sampling block is the
// lever set against VLM repetition loops — pages the model can't read
// make it emit the same lines until the output limit.
const HINTS: Record<string, string> = {
  "llm.ocr.sampling.temperature": "Unset = server default. Pure greedy decoding (0) is the most loop-prone; a small value like 0.1–0.3 lets the model escape a repetition.",
  "llm.ocr.sampling.top_p": "Nucleus sampling cut-off (0–1). Unset = server default.",
  "llm.ocr.sampling.top_k": "Only sample from the k most likely tokens. Sent via extra_body (vLLM, SGLang, llama.cpp, Ollama).",
  "llm.ocr.sampling.min_p": "Drop tokens below this fraction of the top token's probability (0–1). Sent via extra_body.",
  "llm.ocr.sampling.max_tokens": "Hard output cap per OCR call. Set it so a looping page fails fast instead of generating until the context window is full — Autodetect's tok/page measurement × images per request plus headroom is a good value.",
  "llm.ocr.sampling.presence_penalty": "Flat penalty on tokens that already appeared (-2–2). Qwen-VL's own recommendation against transcription loops is up to 1.5.",
  "llm.ocr.sampling.frequency_penalty": "Penalty growing with each recurrence (-2–2). Milder alternative to presence penalty; high values distort legitimately repetitive documents (tables).",
  "llm.ocr.sampling.repetition_penalty": "Multiplicative penalty on seen tokens (vLLM/SGLang-style, via extra_body). 1 = off; anti-loop range is typically 1.05–1.3.",
};

// Fields that are numeric even when currently unset (null) — the type
// can't be sniffed from the value then.
const NUMERIC_FIELDS = new Set([
  "temperature",
  "top_p",
  "top_k",
  "min_p",
  "max_tokens",
  "presence_penalty",
  "frequency_penalty",
  "repetition_penalty",
  "timeout_seconds",
  "max_concurrent",
]);

const CHOICES: Record<string, { value: string; label: string }[]> = {
  thinking: ["server_default", "on", "off"].map((v) => ({ value: v, label: v })),
  apply_policy: [
    { value: "review", label: "review" },
    { value: "auto", label: "auto (journaled)" },
  ],
};

export function FieldEditor({
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
  // Nullable numeric levers (sampling) have no value to sniff the type
  // from when unset — recognize them by name. Emptying the input clears
  // the override (null) instead of sending an unparsable "".
  const numeric = typeof row.value === "number" || NUMERIC_FIELDS.has(field);
  return (
    <Input
      aria-label={row.key}
      type={numeric ? "number" : "text"}
      step={numeric ? "any" : undefined}
      disabled={disabled}
      value={String(value ?? "")}
      onChange={(e) =>
        onChange(
          numeric
            ? e.target.value === ""
              ? null
              : Number(e.target.value)
            : e.target.value,
        )
      }
      className="h-8"
    />
  );
}

function testSummary(t: LlmTest): string {
  if (!t.ok) return `✗ ${t.error ?? "failed"}`;
  const reply = t.reply ? ` · “${t.reply}”` : "";
  return `✓ ${t.model} reachable · ${t.latency_ms} ms${reply}`;
}

function detectSummary(d: LlmDetect, profile: ProbeProfile): string {
  const bits: string[] = [];
  if (d.context_length != null)
    bits.push(
      `context window ${d.context_length.toLocaleString()} (${d.context_source})`,
    );
  if (profile === "ocr") {
    if (d.max_images != null)
      bits.push(
        d.max_images_exact
          ? `server cap: ${d.max_images} images/request`
          : `no server cap up to ${d.max_images}`,
      );
    if (d.tokens_per_image != null)
      bits.push(
        `≈ ${d.tokens_per_image.toLocaleString()} tok/page @ ${d.render_dpi} DPI`,
      );
    if (d.images_in_context != null)
      bits.push(
        `context fits ~${d.images_in_context} page${d.images_in_context === 1 ? "" : "s"} (incl. output)`,
      );
  }
  if (bits.length === 0) return `✗ ${d.error ?? "nothing detected"}`;
  const applied = Object.keys(d.suggestions).length > 0;
  return `✓ ${bits.join(" · ")}${applied ? " — suggestion filled into the form, review & save" : ""}`;
}

/** Connectivity + capability probes for one LLM profile. Detection
 * results only ever land in the FORM (as a draft) — the admin reviews
 * and saves like any hand-typed value. */
function LlmDiagnostics({
  profile,
  onSuggest,
}: {
  profile: ProbeProfile;
  onSuggest: (values: Record<string, number>) => void;
}) {
  const canDetect = profile === "agent" || profile === "ocr";
  const test = useMutation({ mutationFn: () => api.testLlm(profile) });
  const detect = useMutation({
    mutationFn: () => {
      // The Autodetect button only renders for these two profiles.
      if (profile !== "agent" && profile !== "ocr")
        throw new Error("detection is only available for completion profiles");
      return api.detectLlm(profile);
    },
    onSuccess: (d) => {
      const s = d.suggestions as Record<string, number>;
      if (Object.keys(s).length > 0) onSuggest(s);
    },
  });
  const busy = test.isPending || detect.isPending;
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <Tip
          content={
            profile === "ocr"
              ? "Sends one tiny completion WITH a test image — verifies the endpoint really serves vision"
              : profile === "embeddings"
                ? "Embeds one test string via the production client — shows the vector dimension"
                : profile === "reranker"
                  ? "Reranks an obvious two-document pair — shows whether the model actually ranks"
                  : "Sends one tiny completion to the configured endpoint"
          }
        >
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            disabled={busy}
            onClick={() => test.mutate()}
          >
            {test.isPending ? "Testing…" : "Test connection"}
          </Button>
        </Tip>
        {canDetect && (
          <Tip
            content={
              profile === "ocr"
                ? "Probes the server's image cap, measures the token cost of one page at your render DPI, and predicts how many pages fit the context window"
                : "Reads the server's context window from its metadata endpoint (vLLM, llama.cpp, Ollama) and suggests an input clamp"
            }
          >
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              disabled={busy}
              onClick={() => detect.mutate()}
            >
              {detect.isPending ? "Detecting…" : "Autodetect"}
            </Button>
          </Tip>
        )}
      </div>
      {test.data && (
        <p
          className={`text-xs ${test.data.ok ? "text-muted-foreground" : "text-destructive"}`}
        >
          {testSummary(test.data)}
        </p>
      )}
      {detect.data && (
        <p
          className={`text-xs ${
            detect.data.error && Object.keys(detect.data.suggestions).length === 0 && detect.data.context_length == null && detect.data.max_images == null
              ? "text-destructive"
              : "text-muted-foreground"
          }`}
        >
          {detectSummary(detect.data, profile)}
        </p>
      )}
      <ErrorNotice error={test.error} />
      <ErrorNotice error={detect.error} />
    </div>
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
              {group.probe && isAdmin && (
                <div className="mb-3">
                  <LlmDiagnostics
                    profile={group.probe}
                    onSuggest={(values) =>
                      setDraft((d) => ({ ...d, ...values }))
                    }
                  />
                </div>
              )}
              {groupRows.map((row) => (
                <div
                  key={row.key}
                  className="grid grid-cols-[13rem_1fr_auto] items-center gap-3"
                >
                  {HINTS[row.key] ? (
                    <Tip content={HINTS[row.key]}>
                      <Label className="cursor-help font-normal text-muted-foreground underline decoration-dotted underline-offset-2">
                        {LABELS[row.key.split(".").pop()!] ?? row.key}
                      </Label>
                    </Tip>
                  ) : (
                    <Label className="font-normal text-muted-foreground">
                      {LABELS[row.key.split(".").pop()!] ?? row.key}
                    </Label>
                  )}
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
