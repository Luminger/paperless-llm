// Webhook ingress — status, configuration, self-test, and one-click
// setup of the paperless workflow, together on the Paperless tab
// (status without its knobs was half a story on two different tabs).

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { Tip } from "@/components/app/Tip";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../../api";
import { keys } from "../../lib/keys";
import { useAuth } from "../../lib/auth";
import { FieldEditor, LABELS } from "./Models";
import { OnOff, Row, SourceBadge } from "./shared";

export function WebhookCard() {
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const qc = useQueryClient();
  const { data: hook } = useQuery({
    queryKey: keys.webhookStatus(),
    queryFn: api.getWebhookStatus,
  });
  const { data: rows } = useQuery({ queryKey: keys.config(), queryFn: api.getConfig });
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: keys.config() });
    qc.invalidateQueries({ queryKey: keys.webhookStatus() });
    qc.invalidateQueries({ queryKey: keys.settings() });
  };
  const save = useMutation({
    mutationFn: () => api.putConfig(draft),
    onSuccess: () => {
      setDraft({});
      invalidate();
    },
  });
  const setup = useMutation({
    mutationFn: api.setupWebhook,
    onSuccess: invalidate,
  });
  if (!rows) return <LoadingState lines={4} />;
  const webhookRows = rows.filter((r) => r.key.startsWith("webhook."));
  const dirty = Object.keys(draft).length > 0;
  const busy = setup.isPending || save.isPending;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Webhook ingress</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground/70">
          A paperless workflow posts newly added documents to this app, which
          then analyzes them with the defaults below.
        </p>
        <div>
          <Row label="App side">
            {hook == null ? (
              "…"
            ) : (
              <OnOff
                on={hook.secret_configured}
                labels={["secret configured", "no secret — ingress disabled"]}
              />
            )}
          </Row>
          <Row label="Paperless side">
            {hook == null ? (
              "…"
            ) : hook.workflow_found == null ? (
              <span className="text-muted-foreground">
                unknown — this paperless exposes no workflows API to us
              </span>
            ) : hook.workflow_found ? (
              hook.workflow_synced === false ? (
                // Existence is not sync: the workflow still posts OLD
                // values (URL/secret/...) until it is healed.
                <span className="text-amber-600 dark:text-amber-500">
                  workflow OUT OF SYNC ({hook.workflow_drift.join(", ")}) —
                  re-run “Set up automatically” to heal it
                </span>
              ) : (
                <span className="flex items-center gap-2">
                  <OnOff
                    on={hook.workflow_enabled}
                    labels={["workflow active", "workflow DISABLED"]}
                  />
                  <span className="text-muted-foreground">{hook.workflow_name}</span>
                  {hook.workflow_synced === true && (
                    <span className="text-xs text-muted-foreground/70">
                      · settings in sync
                    </span>
                  )}
                </span>
              )
            ) : (
              <span className="text-destructive">
                no paperless workflow posts to this app
              </span>
            )}
          </Row>
          {isAdmin && hook != null && (
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
        </div>
        <div className="space-y-2 border-t pt-3">
          {webhookRows.map((row) => (
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
                <SourceBadge source={row.source} />
              </span>
            </div>
          ))}
          {isAdmin && dirty && (
            <div className="flex items-center gap-2">
              <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
                Save changes
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setDraft({})}>
                Discard
              </Button>
            </div>
          )}
        </div>
        {isAdmin && (
          <div className="space-y-1 border-t pt-3">
            <div className="flex items-center gap-2">
              <Tip content="Creates (or heals) the paperless workflow that posts new documents here — generates a secret first if none is set">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  disabled={busy || dirty}
                  onClick={() => setup.mutate()}
                >
                  {setup.isPending ? "Setting up…" : "Set up automatically"}
                </Button>
              </Tip>
              {dirty && (
                <span className="text-xs text-muted-foreground">
                  save your changes first
                </span>
              )}
            </div>
            {setup.data && (
              <p
                className={`text-xs ${setup.data.ok ? "text-muted-foreground" : "text-destructive"}`}
              >
                {setup.data.ok ? "✓" : "✗"} {setup.data.message}
                {setup.data.secret_generated && " · a new secret was generated"}
              </p>
            )}
            <ErrorNotice error={save.error} />
            <ErrorNotice error={setup.error} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
