// The paperless-ngx instance this app is attached to. Deliberately
// read-only: a wrong URL or credential here would take down the very
// UI needed to fix it, so the connection lives in config file /
// environment only.

import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../../api";
import { keys } from "../../lib/keys";
import { useAuth } from "../../lib/auth";
import { OnOff, Row } from "./shared";

export function PaperlessInfo() {
  const { role } = useAuth();
  const { data: s, error, isLoading } = useQuery({
    queryKey: keys.settings(),
    queryFn: api.getSettingsOverview,
  });
  const { data: hook } = useQuery({
    queryKey: keys.webhookStatus(),
    queryFn: api.getWebhookStatus,
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
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Webhook ingress</CardTitle>
        </CardHeader>
        <CardContent>
          <Row label="App side">
            <OnOff
              on={s.webhook.enabled}
              labels={["secret configured", "no secret — disabled"]}
            />
          </Row>
          <Row label="Paperless side">
            {hook == null ? (
              "…"
            ) : hook.workflow_found == null ? (
              <span className="text-muted-foreground">
                unknown — this paperless exposes no workflows API to us
              </span>
            ) : hook.workflow_found ? (
              <span className="flex items-center gap-2">
                <OnOff
                  on={hook.workflow_enabled}
                  labels={["workflow active", "workflow DISABLED"]}
                />
                <span className="text-muted-foreground">{hook.workflow_name}</span>
              </span>
            ) : (
              <span className="text-destructive">
                no paperless workflow posts to this app
              </span>
            )}
          </Row>
          {s.webhook.enabled && (
            <>
              <Row label="Re-do OCR">{String(s.webhook.redo_ocr)}</Row>
              <Row label="Apply policy">{s.webhook.apply_policy}</Row>
            </>
          )}
          {role === "admin" && hook != null && (
            <Row label="Manage">
              <a
                className="inline-flex items-center gap-1 text-primary hover:underline"
                href={hook.workflows_url}
                target="_blank"
                rel="noreferrer"
              >
                paperless workflows <ExternalLink className="size-3" />
              </a>
            </Row>
          )}
        </CardContent>
      </Card>
      <p className="text-xs text-muted-foreground/70">
        The connection is configured via environment variables or the config
        file only — never at runtime, so a bad value can't lock you out of
        this screen.
      </p>
    </div>
  );
}
