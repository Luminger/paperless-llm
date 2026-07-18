import { useEffect, useState } from "react";
import { keys } from "../lib/keys";
import { useQueryClient } from "@tanstack/react-query";
import type { TranscriptItem } from "../api";

/** Live activity of one running step. The items are REAL
 * TranscriptItems — the streaming view renders through the exact same
 * components as the finished transcript — plus an explicit reconcile
 * key for streaming deltas (never smuggled through data fields). */
export type LiveTranscriptItem = TranscriptItem & { live_key?: string };

export interface LiveActivity {
  tokens: number;
  items: LiveTranscriptItem[];
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
  proposal_id?: number;
}

const EMPTY: LiveActivity = { tokens: 0, items: [] };

function liveItem(over: Partial<LiveTranscriptItem>): LiveTranscriptItem {
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
  } as LiveTranscriptItem;
}

// Model parts get stable keys so deltas UPDATE instead of append.
const partKey = (i: number) => `part:${i}`;

/** Pure reducer: one progress event onto the live state (exported for
 * tests). */
export function reduceProgress(cur: LiveActivity, ev: ProgressEvent): LiveActivity {
  const tokens = ev.tokens ?? cur.tokens;
  if (ev.part != null && ev.part_kind) {
    const key = partKey(ev.part);
    const items = [...cur.items];
    const idx = items.findIndex((it) => it.live_key === key);
    const item = liveItem({
      role: ev.part_kind === "thinking" ? "thinking" : "agent",
      content: ev.content ?? "",
      live_key: key,
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
          proposal_id: ev.proposal_id ?? null,
        } as LiveTranscriptItem;
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
  // False while the EventSource is failing — surfaces "live updates
  // unavailable" (buffering reverse proxies are a classic self-hosting
  // misconfiguration); consumers fall back to polling.
  const [connected, setConnected] = useState(true);
  useEffect(() => {
    if (!Number.isFinite(sessionId)) return;
    // A new session means new step ids: stale live state must not leak.
    setLive({});
    setConnected(true);
    let es: EventSource | null = null;
    let timer: number | undefined;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      es = new EventSource(`/api/sessions/${sessionId}/events`);
      es.onopen = () => {
        setConnected(true);
        // Anything that happened while we were away: refetch once.
        qc.invalidateQueries({ queryKey: keys.session(sessionId) });
      };
      es.onerror = () => {
        setConnected(false);
        // EventSource auto-retries transient network errors, but a
        // stream the server CLOSED (502 during a restart, proxy reset)
        // stays dead — it needs a fresh object. Poll until it sticks.
        if (es?.readyState === EventSource.CLOSED) {
          es.close();
          timer = window.setTimeout(connect, 3000);
        }
      };
      es.onmessage = onMessage;
    };

    const onMessage = (raw: MessageEvent) => {
      let ev: ProgressEvent = {};
      try {
        ev = JSON.parse(raw.data);
      } catch {
        return;
      }
      if (ev.type === "step_progress" && ev.step_id != null) {
        const id = ev.step_id;
        setLive((prev) => ({ ...prev, [id]: reduceProgress(prev[id] ?? EMPTY, ev) }));
        // A proposal just landed mid-run: fetch it so the real card
        // pops into the streaming timeline.
        if (ev.proposal_id != null) {
          qc.invalidateQueries({ queryKey: keys.session(sessionId) });
        }
        return; // other progress — no refetch
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
      qc.invalidateQueries({ queryKey: keys.session(sessionId) });
      qc.invalidateQueries({ queryKey: keys.sessionOcr(sessionId) });
    };
    connect();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      es?.close();
    };
  }, [sessionId, qc]);
  return { live, connected };
}
