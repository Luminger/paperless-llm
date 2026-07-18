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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SimpleSelect } from "@/components/app/SimpleSelect";
import { PageHeader } from "@/components/app/PageHeader";
import { Pager } from "@/components/app/Pager";
import { useUrlNumber } from "../hooks/useUrlState";
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

export function scopeLabel(job: Job): string {
  const p = job.params;
  // Jobs carry a human label from creation; fall back to scope facts.
  if (typeof p.label === "string" && p.label) return p.label;
  if (p.inbox) return "Inbox";
  if (p.untagged_only) return "Untagged documents";
  if (Array.isArray(p.document_ids)) return `${p.document_ids.length} selected documents`;
  return job.kind.replaceAll("_", " ");
}

function NewJob({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<"analyze" | "ocr">("analyze");
  const [scope, setScope] = useState<"inbox" | "tag" | "untagged" | "all">("inbox");
  const [tagId, setTagId] = useState<number | undefined>();
  const [redoOcr, setRedoOcr] = useState(false);
  const [auto, setAuto] = useState(false);
  const [instructions, setInstructions] = useState("");
  const ocrOnly = mode === "ocr";
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
            all_documents: scope === "all",
            tag_id: scope === "tag" ? tagId : undefined,
            redo_ocr: ocrOnly ? undefined : redoOcr,
            ocr_only: ocrOnly || undefined,
            apply_policy: auto ? "auto" : "review",
            instructions: instructions.trim() || undefined,
          });
        }}
      >
        <p className="font-medium">New job</p>
        <RadioGroup
          value={mode}
          onValueChange={(v) => {
            setMode(v as typeof mode);
            if (v === "analyze" && scope === "all") setScope("inbox");
          }}
          className="flex flex-wrap gap-4"
        >
          <Label className="flex items-center gap-1.5 font-normal">
            <RadioGroupItem value="analyze" />
            Analyze metadata
          </Label>
          <Label className="flex items-center gap-1.5 font-normal">
            <RadioGroupItem value="ocr" />
            Re-do OCR only (no analysis)
          </Label>
        </RadioGroup>
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
              ...(ocrOnly ? ([["all", "All documents"]] as const) : []),
            ] as const
          ).map(([key, label]) => (
            <Label key={key} className="flex items-center gap-1.5 font-normal">
              <RadioGroupItem value={key} />
              {label}
            </Label>
          ))}
        </RadioGroup>
        {scope === "tag" && (
          <SimpleSelect
            ariaLabel="job tag"
            className="w-full"
            placeholder="pick a tag…"
            value={tagId != null ? String(tagId) : undefined}
            onValueChange={(v) => setTagId(Number(v))}
            options={(tags ?? [])
              .filter((t) => !t.is_inbox_tag)
              .map((t) => ({ value: String(t.id), label: t.name }))}
          />
        )}
        <div className="flex flex-wrap gap-4 text-sm">
          {!ocrOnly && (
            <Label className="flex items-center gap-1.5 font-normal">
              <Checkbox
                checked={redoOcr}
                onCheckedChange={(v) => setRedoOcr(v === true)}
              />
              re-do OCR first (each document gates for review)
            </Label>
          )}
          <Label className="flex items-center gap-1.5 font-normal">
            <Checkbox checked={auto} onCheckedChange={(v) => setAuto(v === true)} />
            {ocrOnly
              ? "auto-apply the new text (journaled & revertible)"
              : "auto-apply proposals (journaled & revertible)"}
          </Label>
        </div>
        <Textarea
          aria-label="job instructions"
          rows={2}
          placeholder={
            ocrOnly
              ? "Optional OCR instructions (layout hints, language…)"
              : "Optional instructions for the agent…"
          }
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
  const [page, setPage] = useUrlNumber("page", 1);
  const pageSize = 25;
  const { data, error, isLoading } = useQuery({
    queryKey: keys.jobs(page),
    queryFn: () => api.listJobs(page, pageSize),
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
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Job</TableHead>
              <TableHead>Progress</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(jobs ?? []).map((j) => (
              <TableRow key={j.id}>
                <TableCell>
                  <Link
                    className="font-medium hover:text-primary hover:underline"
                    to={`/jobs/${j.id}`}
                  >
                    {scopeLabel(j)}
                  </Link>
                  {j.kind === "webhook_analyze" && (
                    <Badge variant="secondary" className="ml-2 text-blue-700 dark:text-blue-300">
                      webhook
                    </Badge>
                  )}
                  {j.kind === "bulk_ocr" && (
                    <Badge variant="outline" className="ml-2">
                      OCR only
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
                </TableCell>
                <TableCell>
                  <JobProgress job={j} />
                </TableCell>
                <TableCell>
                  <StatusBadge status={j.status} />
                </TableCell>
                <TableCell className="text-right">
                  {(j.status === "queued" || j.status === "running") && (
                    <Button variant="secondary" size="sm" onClick={() => cancel.mutate(j.id)}>
                      Cancel
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <Pager
        page={page}
        pageSize={pageSize}
        count={data?.count ?? 0}
        onPage={setPage}
        label="jobs"
      />
    </div>
  );
}
