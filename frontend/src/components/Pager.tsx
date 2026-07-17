/** Generic pagination control for any paginated list. */
export function Pager({
  page,
  pageSize,
  count,
  onPage,
}: {
  page: number;
  pageSize: number;
  count: number;
  onPage: (p: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(count / pageSize));
  if (pages <= 1) return null;
  return (
    <div className="flex items-center gap-2 text-xs text-zinc-500">
      <button
        className="rounded bg-zinc-100 px-2 py-0.5 hover:bg-zinc-200 disabled:opacity-40"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
        ‹ prev
      </button>
      <span>
        page {page} of {pages} · {count} total
      </span>
      <button
        className="rounded bg-zinc-100 px-2 py-0.5 hover:bg-zinc-200 disabled:opacity-40"
        disabled={page >= pages}
        onClick={() => onPage(page + 1)}
      >
        next ›
      </button>
    </div>
  );
}
