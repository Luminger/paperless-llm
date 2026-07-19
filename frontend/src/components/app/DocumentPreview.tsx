// THE document preview: a clickable thumbnail that opens the archived
// rendition in a dialog with page navigation. Reused wherever a human
// must judge a document with their own eyes (document page today,
// inline in sessions next). Follows the theme — dark mode shows the
// inverted rendition, so pages don't glare.

import { api } from "../../api";
import { Tip } from "./Tip";
import { keys } from "../../lib/keys";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { LoadingState } from "./states";
import { cn } from "@/lib/utils";

// Readable-in-the-dark documents: invert, then rotate hue back so any
// colored stamps/logos keep roughly their color.
const THEMED = "dark:invert dark:hue-rotate-180";

const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];

/** THE paginated page viewer: pager + zoom (sharper render past 150%
 * via the ?dpi= knob) + both-axis panning while zoomed + arrow-key
 * page flips. Shared by the session dock and the viewer dialog — one
 * viewer, one set of behaviors. Pages follow the theme (inverted in
 * dark mode, hue rotated back so stamps keep their color). */
export function PagedDocumentViewer({
  documentId,
  autoFocus = false,
}: {
  documentId: number;
  /** Focus the viewport on mount so arrow keys page immediately
   * (the dialog wants this; the dock must not steal focus). */
  autoFocus?: boolean;
}) {
  const { data: info } = useQuery({
    queryKey: keys.documentPreview(documentId),
    queryFn: () => api.getDocumentPreviewInfo(documentId),
    staleTime: 5 * 60_000,
  });
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const boxRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (autoFocus) boxRef.current?.focus();
  }, [autoFocus]);
  if (!info)
    return (
      <div className="p-4">
        <LoadingState lines={4} />
      </div>
    );
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
      {/* Both-axis scroll = panning while zoomed; arrows flip pages. */}
      <div
        ref={boxRef}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft" && cur > 1) setPage(cur - 1);
          if (e.key === "ArrowRight" && cur < pages) setPage(cur + 1);
        }}
        className="min-h-0 flex-1 overflow-auto p-3 outline-none"
      >
        <img
          key={`${cur}:${dpi}`}
          src={src(cur)}
          alt={`page ${cur}`}
          className={cn("rounded-md border", THEMED)}
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

export function DocumentViewerDialog({
  documentId,
  title,
  open,
  onOpenChange,
}: {
  documentId: number;
  title: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[90vh] flex-col gap-3 sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle className="truncate pr-8">{title}</DialogTitle>
          <DialogDescription className="sr-only">
            Archived document preview
          </DialogDescription>
        </DialogHeader>
        {/* The same viewer as the session dock: pager, zoom, panning.
            Radix unmounts the content on close, so state is fresh per
            open; autoFocus makes arrow keys page immediately. */}
        <div className="min-h-0 flex-1 overflow-hidden rounded-md border">
          <PagedDocumentViewer documentId={documentId} autoFocus />
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Thumbnail that opens the full viewer. The one component to reuse
 * anywhere a document must be inspectable. */
export function DocumentPreview({
  documentId,
  title,
  className,
}: {
  documentId: number;
  title: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Tip content="Open the full preview">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label={`open preview of ${title}`}
          className={cn(
            "group relative shrink-0 cursor-zoom-in overflow-hidden rounded-md border",
            className,
          )}
        >
          <img
            src={`/api/entities/documents/${documentId}/thumb`}
            alt={`preview of ${title}`}
            className={cn("h-full w-full object-cover object-top", THEMED)}
          />
          <span className="absolute inset-0 flex items-center justify-center bg-background/0 opacity-0 transition group-hover:bg-background/40 group-hover:opacity-100">
            <Maximize2 className="size-5" />
          </span>
        </button>
      </Tip>
      <DocumentViewerDialog
        documentId={documentId}
        title={title}
        open={open}
        onOpenChange={setOpen}
      />
    </>
  );
}
