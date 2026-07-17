import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

export interface GeneratingState {
  chars: number;
}

/** Subscribe to a session's SSE stream. Most events are invalidation
 * signals (refetch over REST); high-frequency `generating` progress is
 * surfaced as state instead of triggering refetch storms. EventSource
 * auto-reconnects; the hello event on reconnect refetches, so missed
 * events self-heal. */
export function useSessionEvents(sessionId: number) {
  const qc = useQueryClient();
  const [generating, setGenerating] = useState<GeneratingState | null>(null);
  useEffect(() => {
    if (!Number.isFinite(sessionId)) return;
    const es = new EventSource(`/api/sessions/${sessionId}/events`);
    es.onmessage = (raw) => {
      let ev: { type?: string; chars?: number } = {};
      try {
        ev = JSON.parse(raw.data);
      } catch {
        return;
      }
      if (ev.type === "generating") {
        setGenerating({ chars: ev.chars ?? 0 });
        return; // progress only — no refetch
      }
      if (ev.type === "run_finished" || ev.type === "failed" || ev.type === "run_started") {
        setGenerating(null);
      }
      qc.invalidateQueries({ queryKey: ["session", sessionId] });
      qc.invalidateQueries({ queryKey: ["session-ocr", sessionId] });
    };
    return () => es.close();
  }, [sessionId, qc]);
  return { generating };
}
