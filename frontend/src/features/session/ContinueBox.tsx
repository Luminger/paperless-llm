// Contextual steering: once the current work is settled, the next turn
// can be a free-text message — offered inline at the end of the feed,
// not as a fixed chat box.

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { MessageSquarePlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ErrorNotice } from "@/components/app/states";
import { api } from "../../api";
import { keys } from "../../lib/keys";

export function ContinueBox({ sessionId }: { sessionId: number }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const send = useMutation({
    mutationFn: (content: string) => api.sendMessage(sessionId, content),
    onSuccess: () => {
      setDraft("");
      setOpen(false);
      qc.invalidateQueries({ queryKey: keys.session(sessionId) });
    },
  });

  if (!open) {
    return (
      <button
        className="flex w-full items-center justify-center gap-2 rounded-xl border border-dashed py-3 text-sm text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
        onClick={() => setOpen(true)}
      >
        <MessageSquarePlus className="size-4" />
        Continue this session…
      </button>
    );
  }

  return (
    <form
      className="space-y-2 rounded-xl border border-dashed p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (draft.trim()) send.mutate(draft.trim());
      }}
    >
      <Textarea
        aria-label="steer the agent"
        autoFocus
        rows={2}
        placeholder="Ask a question, request different metadata, point out something the agent missed…"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={!draft.trim() || send.isPending}>
          Send
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      <ErrorNotice error={send.error} />
    </form>
  );
}
