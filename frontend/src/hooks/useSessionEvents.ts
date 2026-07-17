import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

export interface LiveToolCall {
  tool: string;
  args: string;
}

/** Live activity of one running step, assembled from step_progress
 * events before anything is persisted. */
export interface LiveActivity {
  tokens: number;
  textTail: string;
  tools: LiveToolCall[];
}

const EMPTY: LiveActivity = { tokens: 0, textTail: "", tools: [] };

/** SSE subscription: step_changed events invalidate (REST refetch),
 * step_progress events build per-step live state. EventSource
 * auto-reconnects; the hello event triggers a refetch, so missed
 * events self-heal. */
export function useSessionEvents(sessionId: number) {
  const qc = useQueryClient();
  const [live, setLive] = useState<Record<number, LiveActivity>>({});
  useEffect(() => {
    if (!Number.isFinite(sessionId)) return;
    const es = new EventSource(`/api/sessions/${sessionId}/events`);
    es.onmessage = (raw) => {
      let ev: {
        type?: string;
        step_id?: number;
        state?: string;
        tokens?: number;
        text_tail?: string;
        tool?: string;
        args?: string;
      } = {};
      try {
        ev = JSON.parse(raw.data);
      } catch {
        return;
      }
      if (ev.type === "step_progress" && ev.step_id != null) {
        const id = ev.step_id;
        setLive((prev) => {
          const cur = prev[id] ?? EMPTY;
          if (ev.tool) {
            return {
              ...prev,
              [id]: { ...cur, textTail: "", tools: [...cur.tools, { tool: ev.tool, args: ev.args ?? "" }] },
            };
          }
          return {
            ...prev,
            [id]: { ...cur, tokens: ev.tokens ?? cur.tokens, textTail: ev.text_tail ?? cur.textTail },
          };
        });
        return; // progress only — no refetch
      }
      if (ev.type === "step_changed" && ev.step_id != null) {
        const id = ev.step_id;
        if (ev.state !== "running") {
          setLive((prev) => {
            const next = { ...prev };
            delete next[id];
            return next;
          });
        }
      }
      qc.invalidateQueries({ queryKey: ["session", sessionId] });
      qc.invalidateQueries({ queryKey: ["session-ocr", sessionId] });
    };
    return () => es.close();
  }, [sessionId, qc]);
  return { live };
}
