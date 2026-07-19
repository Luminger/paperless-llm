/* The pinned document panel: judge a proposal (or the OCR gate)
 * AGAINST the document without leaving the timeline. Non-modal by
 * design — the existing viewer dialog covers exactly the thing being
 * judged. Sticky at viewport height on wide screens (the timeline
 * scrolls, the document stays), a plain block above the timeline on
 * narrow ones. Zero footprint while closed; state lives in the URL
 * (?doc=pages|text) so review deep-links arrive with it open. */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, PanelRightClose, ZoomIn, ZoomOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tip } from "@/components/app/Tip";
import { LoadingState } from "@/components/app/states";
import { api } from "../../api";
import { keys } from "../../lib/keys";

export type DocPanelTab = "pages" | "text";

const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];

// One page at a time, paginated — with zoom. Page images keep their
// paperless colors in dark mode on purpose: the document is evidence,
// not UI. Past 150% the image is re-requested at the endpoint's max
// DPI so zooming shows detail instead of blur.
function Pages({ documentId }: { documentId: number }) {
  const { data: info } = useQuery({
    queryKey: keys.documentPreview(documentId),
    queryFn: () => api.getDocumentPreviewInfo(documentId),
    staleTime: 5 * 60_000,
  });
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  if (!info) return <LoadingState lines={4} />;
  const pages = Math.max(1, info.pages);
  const cur = Math.min(page, pages);
  const dpi = zoom > 1.5 ? 220 : 130;
  const src = (n: number) =>
    `/api/entities/documents/${documentId}/preview/${n}?dpi=${dpi}`;
  const step = (dir: 1 | -1) => {
    const i = ZOOM_STEPS.indexOf(zoom);
    const next = ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, Math.max(0, i + dir))];
    setZoom(next);
  };
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-0.5 border-b bg-muted/30 px-1.5 py-1">
        <Tip content="Previous page" mayDisable>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label="previous page"
            disabled={cur <= 1}
            onClick={() => setPage(cur - 1)}
          >
            <ChevronLeft className="size-4" />
          </Button>
        </Tip>
        <span className="min-w-12 text-center text-xs whitespace-nowrap text-muted-foreground tabular-nums">
          {cur} / {pages}
        </span>
        <Tip content="Next page" mayDisable>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label="next page"
            disabled={cur >= pages}
            onClick={() => setPage(cur + 1)}
          >
            <ChevronRight className="size-4" />
          </Button>
        </Tip>
        <span className="flex-1" />
        <Tip content="Zoom out" mayDisable>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label="zoom out"
            disabled={zoom <= ZOOM_STEPS[0]}
            onClick={() => step(-1)}
          >
            <ZoomOut className="size-4" />
          </Button>
        </Tip>
        <Tip content="Reset to fit width">
          <button
            className="min-w-12 rounded px-1 text-center text-xs text-muted-foreground tabular-nums hover:bg-accent"
            aria-label="reset zoom"
            onClick={() => setZoom(1)}
          >
            {Math.round(zoom * 100)}%
          </button>
        </Tip>
        <Tip content="Zoom in" mayDisable>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label="zoom in"
            disabled={zoom >= ZOOM_STEPS[ZOOM_STEPS.length - 1]}
            onClick={() => step(1)}
          >
            <ZoomIn className="size-4" />
          </Button>
        </Tip>
      </div>
      {/* Both-axis scroll = panning while zoomed in. */}
      <div className="min-h-0 flex-1 overflow-auto p-3">
        <img
          key={`${cur}:${dpi}`}
          src={src(cur)}
          alt={`page ${cur}`}
          className="rounded-md border bg-white"
          style={{
            width: `${zoom * 100}%`,
            maxWidth: zoom <= 1 ? "100%" : "none",
            margin: zoom <= 1 ? "0 auto" : undefined,
          }}
        />
        {/* Prefetch the neighbor pages — flipping feels instant. */}
        {cur < pages && (
          <img src={src(cur + 1)} alt="" aria-hidden className="hidden" />
        )}
        {cur > 1 && (
          <img src={src(cur - 1)} alt="" aria-hidden className="hidden" />
        )}
      </div>
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
          <Pages documentId={documentId} />
        </div>
        <div className={tab === "text" ? "h-full overflow-y-auto" : "hidden"}>
          <OcrText documentId={documentId} />
        </div>
      </div>
    </aside>
  );
}
