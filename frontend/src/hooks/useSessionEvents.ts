import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { TranscriptItem } from "../api";

/** Live activity of one running step. The items are REAL
 * TranscriptItems — the streaming view renders through the exact same
 * components as the finished transcript. */
export interface LiveActivity {
  tokens: number;
  items: TranscriptItem[];
}

export interface ProgressEvent {
  type?: string;
  step_id?: number;
  state?: string;
  tokens?: number;
  // structured model parts (thinking/text), accumulated server-side
  part?: number;
  part_kind?: "thinking" | "text";
  content?: string;
  // tool lifecycle
  tool?: string;
  args?: string;
  tool_done?: string;
  result?: string;
  rejected?: boolean;
}

const EMPTY: LiveActivity = { tokens: 0, items: [] };

function liveItem(over: Partial<TranscriptItem>): TranscriptItem {
  return {
    role: "agent",
    content: "",
    origin: "chat",
    tool_name: null,
    tool_args: null,
    tool_result: null,
    tool_result_full: null,
    tool_rejected: false,
    timing: null,
    ts: null,
    ...over,
  } as TranscriptItem;
}

// Model parts get stable keys so deltas UPDATE instead of append; the
// key rides in `ts` (unused during streaming).
const partKey = (i: number) => `part:${i}`;

/** Pure reducer: one progress event onto the live state (exported for
 * tests). */
export function reduceProgress(cur: LiveActivity, ev: ProgressEvent): LiveActivity {
  const tokens = ev.tokens ?? cur.tokens;
  if (ev.part != null && ev.part_kind) {
    const key = partKey(ev.part);
    const items = [...cur.items];
    const idx = items.findIndex((it) => it.ts === key);
    const item = liveItem({
      role: ev.part_kind === "thinking" ? "thinking" : "agent",
      content: ev.content ?? "",
      ts: key,
    });
    if (idx >= 0) items[idx] = item;
    else items.push(item);
    return { tokens, items };
  }
  if (ev.tool) {
    let args: Record<string, unknown> | null = null;
    try {
      args = ev.args ? JSON.parse(ev.args) : null;
    } catch {
      args = null;
    }
    return {
      tokens,
      items: [
        ...cur.items,
        liveItem({ role: "tool", tool_name: ev.tool, tool_args: args }),
      ],
    };
  }
  if (ev.tool_done) {
    const items = [...cur.items];
    for (let i = items.length - 1; i >= 0; i--) {
      if (items[i].role === "tool" && items[i].tool_name === ev.tool_done && items[i].tool_result == null) {
        items[i] = {
          ...items[i],
          tool_result: ev.rejected ? `rejected: ${ev.result ?? ""}` : (ev.result ?? ""),
          tool_result_full: ev.result ?? null,
          tool_rejected: ev.rejected === true,
        } as TranscriptItem;
        break;
      }
    }
    return { tokens, items };
  }
  return { ...cur, tokens };
}

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
      let ev: ProgressEvent = {};
      try {
        ev = JSON.parse(raw.data);
      } catch {
        return;
      }
      if (ev.type === "step_progress" && ev.step_id != null) {
        const id = ev.step_id;
        setLive((prev) => ({ ...prev, [id]: reduceProgress(prev[id] ?? EMPTY, ev) }));
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
