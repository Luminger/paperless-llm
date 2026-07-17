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
      <li className="relative pl-8">
        <span className="absolute top-1 left-0 flex h-3.5 w-3.5 items-center justify-center rounded-full border-2 border-dashed border-muted-foreground/40" />
        <Button
          variant="ghost"
          size="sm"
          className="-ml-2 gap-1.5 text-muted-foreground"
          onClick={() => setOpen(true)}
        >
          <MessageSquarePlus className="size-4" />
          Continue this session…
        </Button>
      </li>
    );
  }

  return (
    <li className="relative pl-8">
      <span className="absolute top-1 left-0 flex h-3.5 w-3.5 items-center justify-center rounded-full border-2 border-dashed border-muted-foreground/40" />
      <form
        className="space-y-2"
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
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setOpen(false)}
          >
            Cancel
          </Button>
        </div>
        <ErrorNotice error={send.error} />
      </form>
    </li>
  );
}
