// System: version, storage, worker pool — read-only runtime facts.

import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorNotice, LoadingState } from "@/components/app/states";
import { api } from "../../api";
import { keys } from "../../lib/keys";
import { Row } from "./shared";

export function SystemInfo() {
  const { data: s, error, isLoading } = useQuery({
    queryKey: keys.settings(),
    queryFn: api.getSettingsOverview,
  });
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: api.getMeta });
  if (error) return <ErrorNotice error={error} />;
  if (isLoading || !s) return <LoadingState lines={6} />;
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Application</CardTitle>
        </CardHeader>
        <CardContent>
          <Row label="Version">{meta?.version ?? "…"}</Row>
          <Row label="Database">{s.database}</Row>
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
      <p className="text-xs text-muted-foreground/70">
        Worker pool sizes are fixed at startup (environment / config file).
        Model endpoints and behavior knobs live under{" "}
        <span className="font-medium">Models</span>.
      </p>
    </div>
  );
}
