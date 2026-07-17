import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "../api";
import { keys } from "../lib/keys";
import { formatAgo } from "../lib/format";

/** Transparency bar for paperless-backed lists: when the app last
 * fetched this resource from paperless (server-side truth, covering
 * agent fetches too), whether a fetch is in flight right now, and a
 * manual refresh trigger. */
export function FetchStatus({
  resource,
  isFetching,
  onRefresh,
}: {
  resource: string;
  isFetching: boolean;
  onRefresh: () => void;
}) {
  const { data } = useQuery({
    queryKey: keys.syncStatus(),
    queryFn: api.getSyncStatus,
    refetchInterval: 5000,
  });
  const status = data?.resources[resource];
  const busy = isFetching || (status?.in_flight ?? 0) > 0;
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      {busy ? (
        <span className="flex items-center gap-1.5 text-blue-600 dark:text-blue-400">
          <span className="inline-block size-2 animate-pulse rounded-full bg-current" />
          fetching from paperless…
        </span>
      ) : status?.last_fetched_at ? (
        <span>fetched {formatAgo(status.last_fetched_at)}</span>
      ) : (
        <span>not fetched yet</span>
      )}
      {status?.last_error && (
        <span className="text-destructive">last error: {status.last_error}</span>
      )}
      <Button
        variant="ghost"
        size="sm"
        className="h-6 gap-1 px-2 text-xs"
        onClick={onRefresh}
        disabled={busy}
      >
        <RefreshCw className="size-3" /> Refresh
      </Button>
    </div>
  );
}
