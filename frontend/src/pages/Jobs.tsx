import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { NativeSelect } from "@/components/app/NativeSelect";
import { PageHeader } from "@/components/app/PageHeader";
import { EmptyState, ErrorNotice, LoadingState } from "@/components/app/states";
import { api, type Job, type JobCreate } from "../api";
import { keys } from "../lib/keys";
import { StatusBadge } from "../components/StatusBadge";

function JobProgress({ job }: { job: Job }) {
  const finished = job.done + job.failed;
  const pct = job.total ? Math.round((finished / job.total) * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <Progress value={pct} className="w-32" />
      <span className="text-xs text-muted-foreground">
        {job.done} ok{job.failed ? `, ${job.failed} failed` : ""} / {job.total}
      </span>
    </div>
  );
}

function scopeLabel(job: Job): string {
  const p = job.params;
  if (job.kind === "analyze_entity")
    return `${String(p.entity_type).replaceAll("_", " ")} #${String(p.entity_id)} review`;
  if (p.inbox) return "Inbox";
  if (p.untagged_only) return "Untagged documents";
  if (p.tag_id) return `Tag #${p.tag_id}`;
  if (Array.isArray(p.document_ids))
    return p.document_ids.length === 1
      ? `Document #${p.document_ids[0]}`
      : `${p.document_ids.length} selected documents`;
  return job.kind;
}

function NewJob({ onDone }: { onDone: () => void }) {
  const [scope, setScope] = useState<"inbox" | "tag" | "untagged">("inbox");
  const [tagId, setTagId] = useState<number | undefined>();
  const [redoOcr, setRedoOcr] = useState(false);
  const [auto, setAuto] = useState(false);
  const [instructions, setInstructions] = useState("");
  const { data: tags } = useQuery({
    queryKey: keys.entities("tag"),
    queryFn: api.listTags,
  });

  const create = useMutation({
    mutationFn: (body: JobCreate) => api.createJob(body),
    onSuccess: onDone,
  });

  return (
    <Card className="mb-6 p-4">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate({
            inbox: scope === "inbox",
            untagged_only: scope === "untagged",
            tag_id: scope === "tag" ? tagId : undefined,
            redo_ocr: redoOcr,
            apply_policy: auto ? "auto" : "review",
            instructions: instructions.trim() || undefined,
          });
        }}
      >
        <p className="font-medium">New job</p>
        <RadioGroup
          value={scope}
          onValueChange={(v) => setScope(v as typeof scope)}
          className="flex flex-wrap gap-4"
        >
          {(
            [
              ["inbox", "Inbox documents"],
              ["untagged", "Untagged documents"],
              ["tag", "Documents with tag"],
            ] as const
          ).map(([key, label]) => (
            <Label key={key} className="flex items-center gap-1.5 font-normal">
              <RadioGroupItem value={key} />
              {label}
            </Label>
          ))}
        </RadioGroup>
        {scope === "tag" && (
          <NativeSelect
            aria-label="job tag"
            className="w-full"
            value={tagId ?? ""}
            onChange={(e) =>
              setTagId(e.target.value === "" ? undefined : Number(e.target.value))
            }
          >
            <option value="">pick a tag…</option>
            {(tags ?? [])
              .filter((t) => !t.is_inbox_tag)
              .map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
          </NativeSelect>
        )}
        <div className="flex flex-wrap gap-4 text-sm">
          <Label className="flex items-center gap-1.5 font-normal">
            <Checkbox
              checked={redoOcr}
              onCheckedChange={(v) => setRedoOcr(v === true)}
            />
            re-do OCR (each document gates for review)
          </Label>
          <Label className="flex items-center gap-1.5 font-normal">
            <Checkbox checked={auto} onCheckedChange={(v) => setAuto(v === true)} />
            auto-apply proposals (journaled &amp; revertible)
          </Label>
        </div>
        <Textarea
          aria-label="job instructions"
          rows={2}
          placeholder="Optional instructions for the agent…"
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
        />
        <Button
          type="submit"
          size="sm"
          disabled={create.isPending || (scope === "tag" && !tagId)}
        >
          Start job
        </Button>
        <ErrorNotice error={create.error} />
      </form>
    </Card>
  );
}

export default function Jobs() {
  const qc = useQueryClient();
  const [showNew, setShowNew] = useState(false);
  const { data, error, isLoading } = useQuery({
    queryKey: keys.jobs(),
    queryFn: () => api.listJobs(),
    refetchInterval: (q) =>
      (q.state.data?.results ?? []).some(
        (j) => j.status === "queued" || j.status === "running",
      )
        ? 2000
        : false,
  });
  const jobs = data?.results;
  const cancel = useMutation({
    mutationFn: (id: number) => api.cancelJob(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.jobs() }),
  });

  return (
    <div>
      <PageHeader
        title="Jobs"
        actions={
          <Button size="sm" onClick={() => setShowNew(!showNew)}>
            {showNew ? "Close" : "New job"}
          </Button>
        }
      />
      {showNew && (
        <NewJob
          onDone={() => {
            setShowNew(false);
            qc.invalidateQueries({ queryKey: keys.jobs() });
          }}
        />
      )}
      <ErrorNotice error={error} />
      {isLoading ? (
        <LoadingState lines={3} />
      ) : jobs && jobs.length === 0 ? (
        <EmptyState
          title="No jobs yet."
          hint="A job analyzes a whole set of documents — the inbox, a tag, or a selection."
        />
      ) : (
        <ul className="space-y-2">
          {(jobs ?? []).map((j) => (
            <li key={j.id}>
              <Card className="flex flex-row items-center gap-4 p-3 text-sm">
                <span className="w-12 text-muted-foreground/60">#{j.id}</span>
                <span className="flex-1">
                  {scopeLabel(j)}
                  {j.kind === "webhook_analyze" && (
                    <Badge variant="secondary" className="ml-2 text-blue-700 dark:text-blue-300">
                      webhook
                    </Badge>
                  )}
                  {j.params.apply_policy === "auto" && (
                    <Badge
                      variant="secondary"
                      className="ml-2 text-purple-700 dark:text-purple-300"
                    >
                      auto-apply
                    </Badge>
                  )}
                </span>
                <JobProgress job={j} />
                <StatusBadge status={j.status} />
                {(j.status === "queued" || j.status === "running") && (
                  <Button variant="secondary" size="sm" onClick={() => cancel.mutate(j.id)}>
                    Cancel
                  </Button>
                )}
                <Link className="text-xs text-primary hover:underline" to={`/jobs/${j.id}`}>
                  details
                </Link>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
