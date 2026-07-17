// Uniform loading / empty / error presentation for queries.

import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage } from "@/lib/errors";

export function LoadingState({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2" role="status" aria-label="loading">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed p-8 text-center">
      <p className="text-sm font-medium text-muted-foreground">{title}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground/70">{hint}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function ErrorNotice({ error }: { error: unknown }) {
  if (error == null) return null;
  return (
    <p
      role="alert"
      className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
    >
      {errorMessage(error)}
    </p>
  );
}
