// The paperless-ngx instance this app is attached to. Deliberately
// read-only: a wrong URL or credential here would take down the very
// UI needed to fix it, so the connection lives in config file /
// environment only.

import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../../api";
import { keys } from "../../lib/keys";
import { OnOff, Row } from "./shared";
import { WebhookCard } from "./Webhook";

export function PaperlessInfo() {
  const { data: s, error, isLoading } = useQuery({
    queryKey: keys.settings(),
    queryFn: api.getSettingsOverview,
  });
  if (error) return <ErrorNotice error={error} />;
  if (isLoading || !s) return <LoadingState lines={6} />;
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Instance</CardTitle>
        </CardHeader>
        <CardContent>
          <Row label="Instance">
            <a
              className="text-primary hover:underline"
              href={s.paperless.external_url}
              target="_blank"
              rel="noreferrer"
            >
              {s.paperless.external_url}
            </a>
          </Row>
          <Row label="API endpoint">{s.paperless.base_url}</Row>
          <Row label="App credentials">{s.paperless.auth}</Row>
          <Row label="TLS verification">
            <span className="flex items-center gap-2">
              <OnOff on={s.paperless.verify_tls} labels={["verified", "DISABLED"]} />
              {!s.paperless.verify_tls && (
                <span className="text-xs text-destructive">
                  certificate & host checks are off — self-signed setups only
                </span>
              )}
            </span>
          </Row>
          <Row label="Timeout">{s.paperless.timeout_seconds}s</Row>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Sign-in & roles</CardTitle>
        </CardHeader>
        <CardContent>
          <Row label="Authentication">paperless credentials</Row>
          <Row label="Administrators">paperless superusers</Row>
        </CardContent>
      </Card>
      <WebhookCard />
      <p className="text-xs text-muted-foreground/70">
        The connection above is configured via environment variables or the
        config file only — never at runtime, so a bad value can't lock you
        out of this screen. Webhook settings are runtime-editable.
      </p>
    </div>
  );
}
