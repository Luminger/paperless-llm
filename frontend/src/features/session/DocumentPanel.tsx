/* The pinned document panel: judge a proposal (or the OCR gate)
 * AGAINST the document without leaving the timeline. Non-modal by
 * design — the existing viewer dialog covers exactly the thing being
 * judged. Sticky at viewport height on wide screens (the timeline
 * scrolls, the document stays), a plain block above the timeline on
 * narrow ones. Zero footprint while closed; state lives in the URL
 * (?doc=pages|text) so review deep-links arrive with it open. */

import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tip } from "@/components/app/Tip";
import { LoadingState } from "@/components/app/states";
import { api } from "../../api";
import { keys } from "../../lib/keys";

export type DocPanelTab = "pages" | "text";

// Page images keep their paperless colors in dark mode on purpose —
// the document is evidence, not UI.
function Pages({ documentId }: { documentId: number }) {
  const { data: info } = useQuery({
    queryKey: keys.documentPreview(documentId),
    queryFn: () => api.getDocumentPreviewInfo(documentId),
    staleTime: 5 * 60_000,
  });
  if (!info) return <LoadingState lines={4} />;
  return (
    <div className="space-y-3 p-3">
      {Array.from({ length: info.pages }, (_, i) => (
        <figure key={i}>
          <img
            src={`/api/entities/documents/${documentId}/preview/${i + 1}`}
            alt={`page ${i + 1}`}
            loading="lazy"
            className="w-full rounded-md border bg-white"
          />
          <figcaption className="mt-1 text-center text-[10px] text-muted-foreground/60">
            {i + 1} / {info.pages}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

function OcrText({ documentId }: { documentId: number }) {
  const { data: doc } = useQuery({
    queryKey: keys.document(documentId),
    queryFn: () => api.getDocument(documentId),
  });
  if (!doc) return <LoadingState lines={6} />;
  return (
    <pre className="p-4 font-mono text-xs leading-5 break-words whitespace-pre-wrap">
      {doc.content || "(no stored text)"}
    </pre>
  );
}

export function DocumentPanel({
  documentId,
  tab,
  onTab,
  onClose,
}: {
  documentId: number;
  tab: DocPanelTab;
  onTab: (t: DocPanelTab) => void;
  onClose: () => void;
}) {
  return (
    <aside
      aria-label="document panel"
      className="flex flex-col overflow-hidden rounded-lg border bg-card lg:sticky lg:top-4 lg:h-[calc(100vh-6rem)]"
    >
      <div className="flex items-center gap-2 border-b bg-muted/40 px-2 py-1.5">
        <Tabs value={tab} onValueChange={(v) => onTab(v as DocPanelTab)}>
          <TabsList className="h-8">
            <TabsTrigger value="pages" className="px-3 text-xs">
              Pages
            </TabsTrigger>
            <TabsTrigger value="text" className="px-3 text-xs">
              Text
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <span className="flex-1" />
        <Tip content="Close the document panel">
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label="close document panel"
            onClick={onClose}
          >
            <X className="size-4" />
          </Button>
        </Tip>
      </div>
      {/* max-height on mobile keeps an opened panel from burying the
          timeline; on lg the sticky viewport-height column scrolls. */}
      <div className="max-h-[50vh] min-h-0 flex-1 overflow-y-auto lg:max-h-none">
        {tab === "text" ? (
          <OcrText documentId={documentId} />
        ) : (
          <Pages documentId={documentId} />
        )}
      </div>
    </aside>
  );
}
