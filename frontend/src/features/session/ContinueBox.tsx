// The next turn IS the input: instead of a "continue" button, the end
// of the feed shows the coming turn's box with the message field
// exactly where the user's prompt will render. Sending transforms the
// textbox into text — the real turn then takes the box's place.

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { ErrorNotice } from "@/components/app/states";
import { api } from "../../api";
import { keys } from "../../lib/keys";

export function NextTurnBox({
  sessionId,
  turn,
}: {
  sessionId: number;
  /** The ordinal this turn will get ("Turn 4"). */
  turn: number;
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const send = useMutation({
    mutationFn: (content: string) => api.sendMessage(sessionId, content),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.session(sessionId) });
    },
  });

  const submit = () => {
    const content = draft.trim();
    if (!content || send.isPending) return;
    setSent(content);
    send.mutate(content, {
      onError: () => setSent(null),
    });
  };

  return (
    <Card className="gap-0 overflow-hidden border-dashed py-0">
      <div className="flex h-10 items-center gap-2.5 border-b bg-muted/30 px-4">
        <span className="size-2 shrink-0 rounded-full bg-muted-foreground/25" />
        <span className="text-sm font-medium text-muted-foreground">
          Turn {turn}
        </span>
      </div>
      <div className="px-4 py-3">
        {/* The exact position and styling a user prompt renders with. */}
        <div className="grid grid-cols-[20px_1fr] items-start gap-x-2 rounded-md bg-primary/5 p-2">
          <MessageSquare className="mt-1.5 size-3.5 text-primary" />
          {sent != null ? (
            <p className="text-sm leading-6 whitespace-pre-wrap">{sent}</p>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                submit();
              }}
            >
              <Textarea
                aria-label="steer the agent"
                rows={1}
                placeholder="Ask a question, request different metadata, point out something the agent missed…"
                className="min-h-8 resize-none border-none bg-transparent p-0 text-sm leading-6 shadow-none focus-visible:ring-0 dark:bg-transparent"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    submit();
                  }
                }}
              />
              <div className="mt-2">
                <Button
                  type="submit"
                  size="sm"
                  disabled={!draft.trim() || send.isPending}
                >
                  Send
                </Button>
              </div>
            </form>
          )}
        </div>
        <ErrorNotice error={send.error} />
      </div>
    </Card>
  );
}
