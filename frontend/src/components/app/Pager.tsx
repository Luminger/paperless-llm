// THE pagination row for every paginated list: total count on the
// left, the framework's pagination widget on the right. Numbered
// pages with ellipsis; hidden entirely when there is nothing to page.

import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";

/** 1 … 4 [5] 6 … 20 — current page, its neighbors, first and last. */
function pageItems(page: number, pages: number): (number | "…")[] {
  const wanted = new Set([1, page - 1, page, page + 1, pages]);
  const items: (number | "…")[] = [];
  let prev = 0;
  for (let n = 1; n <= pages; n++) {
    if (!wanted.has(n)) continue;
    if (prev && n - prev > 1) items.push("…");
    items.push(n);
    prev = n;
  }
  return items;
}

export function Pager({
  page,
  pageSize,
  count,
  onPage,
  label = "items",
}: {
  page: number;
  pageSize: number;
  count: number;
  onPage: (p: number) => void;
  /** Noun for the count text ("13 documents"). */
  label?: string;
}) {
  const pages = Math.max(1, Math.ceil(count / pageSize));
  const link = (p: number) => (e: React.MouseEvent) => {
    e.preventDefault();
    if (p >= 1 && p <= pages && p !== page) onPage(p);
  };
  return (
    <div className="mt-2 flex items-center justify-between gap-4">
      <p className="text-xs whitespace-nowrap text-muted-foreground">
        {count} {label}
      </p>
      {pages > 1 && (
        <Pagination className="mx-0 w-auto justify-end">
          <PaginationContent>
            <PaginationItem>
              <PaginationPrevious
                href="#"
                aria-disabled={page <= 1}
                className={page <= 1 ? "pointer-events-none opacity-50" : ""}
                onClick={link(page - 1)}
              />
            </PaginationItem>
            {pageItems(page, pages).map((it, i) => (
              <PaginationItem key={`${it}-${i}`}>
                {it === "…" ? (
                  <PaginationEllipsis />
                ) : (
                  <PaginationLink
                    href="#"
                    isActive={it === page}
                    onClick={link(it)}
                  >
                    {it}
                  </PaginationLink>
                )}
              </PaginationItem>
            ))}
            <PaginationItem>
              <PaginationNext
                href="#"
                aria-disabled={page >= pages}
                className={page >= pages ? "pointer-events-none opacity-50" : ""}
                onClick={link(page + 1)}
              />
            </PaginationItem>
          </PaginationContent>
        </Pagination>
      )}
    </div>
  );
}
