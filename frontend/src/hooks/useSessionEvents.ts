import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

export interface LiveToolCall {
  tool: string;
  args: string;
}

/** What's happening in the current agent run, assembled from SSE events
 * before anything is persisted: tool calls so far, streamed token count,
 * and a tail preview of the visible output text. */
export interface LiveActivity {
  tokens: number;
  textTail: string;
  tools: LiveToolCall[];
}

const EMPTY: LiveActivity = { tokens: 0, textTail: "", tools: [] };

/** Subscribe to a session's SSE stream. Most events are invalidation
 * signals (refetch over REST); high-frequency progress events build the
 * live-activity state instead of triggering refetch storms. EventSource
 * auto-reconnects; the hello event on reconnect refetches, so missed
 * events self-heal. */
export function useSessionEvents(sessionId: number) {
  const qc = useQueryClient();
  const [live, setLive] = useState<LiveActivity | null>(null);
  useEffect(() => {
    if (!Number.isFinite(sessionId)) return;
    const es = new EventSource(`/api/sessions/${sessionId}/events`);
    es.onmessage = (raw) => {
      let ev: { type?: string; tokens?: number; text_tail?: string; tool?: string; args?: string } =
        {};
      try {
        ev = JSON.parse(raw.data);
      } catch {
        return;
      }
      switch (ev.type) {
        case "generating":
          setLive((prev) => ({
            ...(prev ?? EMPTY),
            tokens: ev.tokens ?? 0,
            textTail: ev.text_tail ?? "",
          }));
          return; // progress only — no refetch
        case "tool_called":
          setLive((prev) => ({
            ...(prev ?? EMPTY),
            textTail: "", // a new model turn begins after a tool call
            tools: [...(prev?.tools ?? []), { tool: ev.tool ?? "?", args: ev.args ?? "" }],
          }));
          return;
        case "run_started":
          setLive(EMPTY);
          break;
        case "run_finished":
        case "failed":
          setLive(null);
          break;
      }
      qc.invalidateQueries({ queryKey: ["session", sessionId] });
      qc.invalidateQueries({ queryKey: ["session-ocr", sessionId] });
    };
    return () => es.close();
  }, [sessionId, qc]);
  return { live };
}
