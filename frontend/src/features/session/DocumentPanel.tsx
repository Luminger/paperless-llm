/* The document dock: judge a proposal (or the OCR gate) AGAINST the
 * document without leaving the timeline. Built on the framework's
 * Sidebar primitive (side="right", floating, offcanvas): the provider
 * reserves the width in-flow, the rail on the viewport edge collapses
 * and restores it, mobile automatically becomes a sheet, and ⌘/Ctrl+B
 * toggles it. State lives in the URL (?doc=pages|text) so review
 * deep-links arrive with the evidence open.
 *
 * Both tabs STAY MOUNTED with the inactive one css-hidden: switching
 * (or collapsing!) discards nothing — zoom, page and pan positions
 * survive. */

import { useQuery } from "@tanstack/react-query";
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarRail,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tip } from "@/components/app/Tip";
import { LoadingState } from "@/components/app/states";
import { PagedDocumentViewer } from "@/components/app/DocumentPreview";
import { Button } from "@/components/ui/button";
import { PanelLeft, PanelRight } from "lucide-react";
import {
  setDocPanelSide,
  useDocPanelSide,
  type DocPanelSide,
} from "../../lib/prefs";
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
}: {
  documentId: number;
  tab: DocPanelTab;
  onTab: (t: DocPanelTab) => void;
}) {
  const side = useDocPanelSide();
  const other: DocPanelSide = side === "right" ? "left" : "right";
  return (
    <Sidebar
      side={side}
      variant="floating"
      collapsible="offcanvas"
      aria-label="document panel"
      // Anchor below the sticky app header instead of inset-y-0/h-svh.
      className="top-14 h-[calc(100svh-3.5rem)]"
    >
      <SidebarHeader className="flex-row items-center gap-2 border-b border-sidebar-border p-1.5">
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
        <Tip content={`Move the panel to the ${other} side (saved to your preferences)`}>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label={`move panel to the ${other} side`}
            onClick={() => setDocPanelSide(other)}
          >
            {other === "left" ? (
              <PanelLeft className="size-4" />
            ) : (
              <PanelRight className="size-4" />
            )}
          </Button>
        </Tip>
        <Tip content="Collapse the panel — the edge rail (or ⌘/Ctrl+B) brings it back">
          <SidebarTrigger
            aria-label="collapse document panel"
            className="size-7"
          />
        </Tip>
      </SidebarHeader>
      <SidebarContent className="overflow-hidden">
        <div className={tab === "pages" ? "h-full min-h-0" : "hidden"}>
          <PagedDocumentViewer documentId={documentId} />
        </div>
        <div
          className={tab === "text" ? "h-full min-h-0 overflow-y-auto" : "hidden"}
        >
          <OcrText documentId={documentId} />
        </div>
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  );
}
