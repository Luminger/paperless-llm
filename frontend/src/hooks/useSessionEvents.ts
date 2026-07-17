import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

/** Subscribe to a session's SSE stream. Events are invalidation
 * signals: on anything, refetch the session (and OCR review) over REST.
 * EventSource auto-reconnects; the server's hello event on reconnect
 * triggers a refetch, so missed events self-heal. */
export function useSessionEvents(sessionId: number) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!Number.isFinite(sessionId)) return;
    const es = new EventSource(`/api/sessions/${sessionId}/events`);
    es.onmessage = () => {
      qc.invalidateQueries({ queryKey: ["session", sessionId] });
      qc.invalidateQueries({ queryKey: ["session-ocr", sessionId] });
    };
    return () => es.close();
  }, [sessionId, qc]);
}
