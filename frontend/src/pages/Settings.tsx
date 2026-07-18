import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { SimpleSelect } from "@/components/app/SimpleSelect";
import { PageHeader } from "@/components/app/PageHeader";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../api";
import { keys } from "../lib/keys";
import {
  DATE_PREFS,
  TIME_PREFS,
  formatDateTime,
  getDateTimePrefs,
  setDateTimePrefs,
  timeZoneOptions,
  type DatePref,
  type TimePref,
} from "../lib/format";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[11rem_1fr] gap-3 border-b border-border/50 py-1.5 text-sm last:border-b-0">
      <div className="text-muted-foreground">{label}</div>
      <div className="min-w-0 truncate font-mono text-xs leading-5">{children}</div>
    </div>
  );
}

function OnOff({ on, labels = ["enabled", "disabled"] }: { on: boolean; labels?: [string, string] }) {
  return (
    <Badge
      variant="secondary"
      className={on ? "text-primary" : "text-muted-foreground"}
    >
      {on ? labels[0] : labels[1]}
    </Badge>
  );
}

/** Client-side preference (stored locally, like the theme): how dates
 * and times render everywhere in the app. */
function DateTimePrefs() {
  const [prefs, setPrefs] = useState(getDateTimePrefs);
  const save = useMutation({
    mutationFn: (p: { date: DatePref; time: TimePref; timeZone: string }) =>
      api.putPrefs({
        date_format: p.date,
        time_format: p.time,
        time_zone: p.timeZone,
      }),
  });
  const update = (date: DatePref, time: TimePref, timeZone: string) => {
    setDateTimePrefs(date, time, timeZone);   // instant, local cache
    setPrefs({ date, time, timeZone });
    save.mutate({ date, time, timeZone });    // persisted server-side
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Date &amp; time</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-[11rem_1fr] items-center gap-3">
          <Label className="font-normal text-muted-foreground">Date format</Label>
          <SimpleSelect
            ariaLabel="date format"
            value={prefs.date}
            onValueChange={(v) => update(v as DatePref, prefs.time, prefs.timeZone)}
            options={DATE_PREFS.map((o) => ({ value: o.value, label: o.label }))}
          />
          <Label className="font-normal text-muted-foreground">Time format</Label>
          <SimpleSelect
            ariaLabel="time format"
            value={prefs.time}
            onValueChange={(v) => update(prefs.date, v as TimePref, prefs.timeZone)}
            options={TIME_PREFS.map((o) => ({ value: o.value, label: o.label }))}
          />
          <Label className="font-normal text-muted-foreground">Timezone</Label>
          <SimpleSelect
            ariaLabel="timezone"
            value={prefs.timeZone}
            onValueChange={(v) => update(prefs.date, prefs.time, v)}
            options={[
              { value: "system", label: "System timezone" },
              ...timeZoneOptions().map((z) => ({ value: z, label: z })),
            ]}
          />
        </div>
        <p className="text-xs text-muted-foreground/70">
          Preview: {formatDateTime(new Date().toISOString())}. Saved on the
          server — every browser and device shows the same formats.
          Timestamps themselves are always stored in UTC.
        </p>
        {save.error && (
          <p className="text-xs text-destructive">
            could not save to the server — the setting applies locally for now
          </p>
        )}
      </CardContent>
    </Card>
  );
}

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

function PromptTuning({
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

export default function Settings() {
  const { data: s, error, isLoading } = useQuery({
    queryKey: keys.settings(),
    queryFn: api.getSettingsOverview,
  });

  if (error) return <ErrorNotice error={error} />;
  if (isLoading || !s) return <LoadingState lines={6} />;

  return (
    <div>
      <PageHeader title="Settings" />
      <p className="-mt-2 mb-4 text-sm text-muted-foreground">
        Display preferences are yours to change; the server configuration below
        is read-only — it lives in the config file and environment. Secrets stay
        on the server.
      </p>

      <div className="grid gap-4 md:grid-cols-2">
        <DateTimePrefs />
        <PromptTuning defaults={s.prompt_defaults} />
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Agent model</CardTitle>
          </CardHeader>
          <CardContent>
            <Row label="Endpoint">{s.llm_agent.base_url}</Row>
            <Row label="Model">{s.llm_agent.model}</Row>
            <Row label="Max concurrent">{s.llm_agent.max_concurrent}</Row>
            <Row label="Streaming">
              <OnOff on={s.llm_agent.supports_streaming === true} labels={["on", "off"]} />
            </Row>
            <Row label="Thinking">{s.llm_agent.thinking}</Row>
            <Row label="Input token clamp">{s.llm_agent.max_input_tokens}</Row>
            <Row label="Tool iterations">{s.llm_agent.max_tool_iterations}</Row>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">OCR model</CardTitle>
          </CardHeader>
          <CardContent>
            <Row label="Endpoint">{s.llm_ocr.base_url}</Row>
            <Row label="Model">{s.llm_ocr.model}</Row>
            <Row label="Dedicated profile">
              <OnOff
                on={s.llm_ocr.configured}
                labels={["yes", "falls back to agent"]}
              />
            </Row>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Embeddings &amp; reranker</CardTitle>
          </CardHeader>
          <CardContent>
            <Row label="Embeddings">
              <OnOff on={s.llm_embeddings.configured} />
            </Row>
            {s.llm_embeddings.configured && (
              <>
                <Row label="Endpoint">{s.llm_embeddings.base_url}</Row>
                <Row label="Model">{s.llm_embeddings.model}</Row>
              </>
            )}
            <Row label="Reranker">
              <OnOff on={s.llm_reranker.configured} />
            </Row>
            {s.llm_reranker.configured && (
              <Row label="Reranker model">{s.llm_reranker.model}</Row>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Paperless</CardTitle>
          </CardHeader>
          <CardContent>
            <Row label="API endpoint">{s.paperless.base_url}</Row>
            <Row label="External URL">{s.paperless.external_url}</Row>
            <Row label="Authentication">{s.paperless.auth}</Row>
            <Row label="Timeout">{s.paperless.timeout_seconds}s</Row>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Queue &amp; retries</CardTitle>
          </CardHeader>
          <CardContent>
            <Row label="Interactive workers">{s.queue.interactive_concurrency}</Row>
            <Row label="Batch workers">{s.queue.batch_concurrency}</Row>
            <Row label="Automatic retries">{s.queue.retry_attempts}</Row>
            <Row label="Retry delay">{s.queue.retry_delay_seconds}s</Row>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Webhook &amp; storage</CardTitle>
          </CardHeader>
          <CardContent>
            <Row label="Webhook ingress">
              <OnOff on={s.webhook.enabled} />
            </Row>
            {s.webhook.enabled && (
              <>
                <Row label="Webhook re-OCR">{String(s.webhook.redo_ocr)}</Row>
                <Row label="Webhook policy">{s.webhook.apply_policy}</Row>
              </>
            )}
            <Row label="Database">{s.database}</Row>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
