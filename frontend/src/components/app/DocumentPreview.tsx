// THE document preview: a clickable thumbnail that opens the archived
// rendition in a dialog with page navigation. Reused wherever a human
// must judge a document with their own eyes (document page today,
// inline in sessions next). Follows the theme — dark mode shows the
// inverted rendition, so pages don't glare.

import { api } from "../../api";
import { Tip } from "./Tip";
import { keys } from "../../lib/keys";
import { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Maximize2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// Readable-in-the-dark documents: invert, then rotate hue back so any
// colored stamps/logos keep roughly their color.
const THEMED = "dark:invert dark:hue-rotate-180";

function Pager({
  page,
  pages,
  onPage,
}: {
  page: number;
  pages: number;
  onPage: (p: number) => void;
}) {
  return (
    <div className="flex items-center justify-center gap-3">
      <Tip content="Previous page" mayDisable>
        <Button
          variant="outline"
          size="sm"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          aria-label="previous page"
        >
          <ChevronLeft className="size-4" />
        </Button>
      </Tip>
      <span className="text-sm whitespace-nowrap text-muted-foreground">
        Page {page} of {pages}
      </span>
      <Tip content="Next page" mayDisable>
        <Button
          variant="outline"
          size="sm"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
          aria-label="next page"
        >
          <ChevronRight className="size-4" />
        </Button>
      </Tip>
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
  const [page, setPage] = useState(1);
  const { data: info } = useQuery({
    // Through api.ts + keys.ts (AUDIT FP-L4): r.ok checked, 401s
    // dispatch pllm:unauthorized like every other call.
    queryKey: keys.documentPreview(documentId),
    queryFn: () => api.getDocumentPreviewInfo(documentId),
    enabled: open,
    staleTime: 5 * 60_000,
  });
  useEffect(() => {
    if (open) setPage(1);
  }, [open, documentId]);
  const pages = info?.pages ?? 1;

  const onKey = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowLeft" && page > 1) setPage(page - 1);
      if (e.key === "ArrowRight" && page < pages) setPage(page + 1);
    },
    [page, pages],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="flex h-[90vh] flex-col gap-3 sm:max-w-3xl"
        onKeyDown={onKey}
      >
        <DialogHeader>
          <DialogTitle className="truncate pr-8">{title}</DialogTitle>
          <DialogDescription className="sr-only">
            Archived document preview
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-auto rounded-md border bg-muted/30">
          <img
            key={page}
            src={`/api/entities/documents/${documentId}/preview/${page}?dpi=160`}
            alt={`page ${page}`}
            className={cn("mx-auto h-auto w-full max-w-full", THEMED)}
          />
        </div>
        {pages > 1 && <Pager page={page} pages={pages} onPage={setPage} />}
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
