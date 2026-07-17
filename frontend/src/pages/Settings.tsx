import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/app/NativeSelect";
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
  const update = (date: DatePref, time: TimePref) => {
    setDateTimePrefs(date, time);
    setPrefs({ date, time });
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Date &amp; time</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-[11rem_1fr] items-center gap-3">
          <Label htmlFor="pref-date" className="font-normal text-muted-foreground">
            Date format
          </Label>
          <NativeSelect
            id="pref-date"
            aria-label="date format"
            value={prefs.date}
            onChange={(e) => update(e.target.value as DatePref, prefs.time)}
          >
            {DATE_PREFS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </NativeSelect>
          <Label htmlFor="pref-time" className="font-normal text-muted-foreground">
            Time format
          </Label>
          <NativeSelect
            id="pref-time"
            aria-label="time format"
            value={prefs.time}
            onChange={(e) => update(prefs.date, e.target.value as TimePref)}
          >
            {TIME_PREFS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <p className="text-xs text-muted-foreground/70">
          Preview: {formatDateTime(new Date().toISOString())} — stored in this
          browser, applies everywhere in the app.
        </p>
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
