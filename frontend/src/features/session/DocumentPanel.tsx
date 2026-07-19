/* The pinned document panel: judge a proposal (or the OCR gate)
 * AGAINST the document without leaving the timeline. Non-modal by
 * design — the existing viewer dialog covers exactly the thing being
 * judged. Sticky at viewport height on wide screens (the timeline
 * scrolls, the document stays), a plain block above the timeline on
 * narrow ones. Zero footprint while closed; state lives in the URL
 * (?doc=pages|text) so review deep-links arrive with it open. */

import { useQuery } from "@tanstack/react-query";
import { PanelRightClose } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tip } from "@/components/app/Tip";
import { LoadingState } from "@/components/app/states";
import { PagedDocumentViewer } from "@/components/app/DocumentPreview";
import { api } from "../../api";
import { keys } from "../../lib/keys";

export type DocPanelTab = "pages" | "text";

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
      className="flex flex-col overflow-hidden rounded-lg border bg-card shadow-sm lg:h-full"
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
        <Tip content="Collapse the panel (the edge tab brings it back)">
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label="collapse document panel"
            onClick={onClose}
          >
            <PanelRightClose className="size-4" />
          </Button>
        </Tip>
      </div>
      {/* max-height on mobile keeps an opened panel from burying the
          timeline; each tab owns its scrolling (Pages pans both axes
          while zoomed, Text scrolls vertically). Both tabs STAY
          MOUNTED and the inactive one is css-hidden: switching back
          restores zoom, page AND pan/scroll positions exactly. */}
      <div className="max-h-[50vh] min-h-0 flex-1 lg:max-h-none">
        <div className={tab === "pages" ? "h-full" : "hidden"}>
          <PagedDocumentViewer documentId={documentId} />
        </div>
        <div className={tab === "text" ? "h-full overflow-y-auto" : "hidden"}>
          <OcrText documentId={documentId} />
        </div>
      </div>
    </aside>
  );
}
