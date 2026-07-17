import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

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
    queryKey: ["sync-status"],
    queryFn: api.getSyncStatus,
    refetchInterval: 5000,
  });
  const status = data?.resources[resource];
  const busy = isFetching || (status?.in_flight ?? 0) > 0;
  return (
    <div className="flex items-center gap-2 text-xs text-zinc-400">
      {busy ? (
        <span className="flex items-center gap-1 text-blue-600">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" />
          fetching from paperless…
        </span>
      ) : status?.last_fetched_at ? (
        <span>
          fetched {new Date(status.last_fetched_at).toLocaleTimeString()}
        </span>
      ) : (
        <span>not fetched yet</span>
      )}
      {status?.last_error && (
        <span className="text-red-600">last error: {status.last_error}</span>
      )}
      <button
        className="rounded bg-zinc-100 px-2 py-0.5 text-zinc-600 hover:bg-zinc-200"
        onClick={onRefresh}
        disabled={busy}
      >
        Refresh
      </button>
    </div>
  );
}
