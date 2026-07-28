// THE connectivity surface: one fixed bottom-right toast style for
// "something can't reach something", always saying WHAT it does about
// it — a live countdown to the next attempt, "reconnecting…" while one
// is in flight, and a retry-now escape hatch. App-level backend
// reachability and the session view's live stream both render through
// this; never scattered ad-hoc banners.

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

/** Seconds until `target` (epoch ms), ticking twice a second. */
function useCountdown(target: number | null): number {
  const [left, setLeft] = useState(0);
  useEffect(() => {
    if (target == null) return;
    const tick = () => setLeft(Math.max(0, Math.ceil((target - Date.now()) / 1000)));
    tick();
    const t = setInterval(tick, 500);
    return () => clearInterval(t);
  }, [target]);
  return target == null ? 0 : left;
}

export function ConnectionToast({
  show,
  label,
  nextRetryAt,
  retrying = false,
  onRetryNow,
}: {
  show: boolean;
  /** What is unreachable ("Server unreachable", "Live updates unavailable"). */
  label: React.ReactNode;
  /** Epoch ms of the next automatic attempt (drives the countdown). */
  nextRetryAt?: number | null;
  /** An attempt is in flight right now. */
  retrying?: boolean;
  onRetryNow?: () => void;
}) {
  const left = useCountdown(nextRetryAt ?? null);
  if (!show) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed right-4 bottom-4 z-50 flex items-center gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning shadow-md backdrop-blur-sm"
    >
      <span className="inline-block size-2 animate-pulse rounded-full bg-warning" />
      <span>
        {label}
        {" — "}
        {retrying || (nextRetryAt != null && left === 0)
          ? "reconnecting…"
          : nextRetryAt != null
            ? `retrying in ${left}s`
            : "reconnecting…"}
      </span>
      {onRetryNow && !retrying && (
        <button
          className="font-medium underline underline-offset-2 hover:opacity-80"
          onClick={onRetryNow}
        >
          retry now
        </button>
      )}
    </div>
  );
}

// Backoff for health probes: quick first, patient later.
const DELAYS_MS = [3000, 5000, 10000, 20000, 30000];

/** Watches the query cache: a network-level failure (server gone,
 * connection refused) starts an app-wide health-probe loop with
 * backoff; the toast narrates every step. The next successful probe
 * (or any successful query) clears it and refetches everything.
 * Per-view error notices stay — they explain SPECIFIC failures; this
 * covers "the backend is unreachable". */
export function ConnectivityProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [down, setDown] = useState(false);
  const [nextRetryAt, setNextRetryAt] = useState<number | null>(null);
  const [probing, setProbing] = useState(false);
  const attempt = useRef(0);
  const timer = useRef<number | undefined>(undefined);

  const recovered = () => {
    window.clearTimeout(timer.current);
    attempt.current = 0;
    setDown(false);
    setProbing(false);
    setNextRetryAt(null);
  };

  const probe = async () => {
    setProbing(true);
    setNextRetryAt(null);
    try {
      const r = await fetch("/api/health", { signal: AbortSignal.timeout(4000) });
      if (!r.ok) throw new Error(String(r.status));
      recovered();
      // Everything on screen was starved while the server was away.
      qc.invalidateQueries();
    } catch {
      setProbing(false);
      schedule();
    }
  };

  const schedule = () => {
    const delay = DELAYS_MS[Math.min(attempt.current, DELAYS_MS.length - 1)];
    attempt.current += 1;
    setNextRetryAt(Date.now() + delay);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(probe, delay);
  };

  useEffect(() => {
    return qc.getQueryCache().subscribe((event) => {
      if (event.type !== "updated") return;
      const q = event.query;
      if (q.state.status === "error" && q.state.error instanceof TypeError) {
        // fetch() network failures are TypeErrors ("Failed to fetch").
        setDown((was) => {
          if (!was) {
            attempt.current = 0;
            schedule();
          }
          return true;
        });
      } else if (q.state.status === "success") {
        setDown((was) => {
          if (was) recovered();
          return false;
        });
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qc]);

  return (
    <>
      {children}
      <ConnectionToast
        show={down}
        label="Server unreachable"
        nextRetryAt={nextRetryAt}
        retrying={probing}
        onRetryNow={probe}
      />
    </>
  );
}
