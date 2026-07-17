const colors: Record<string, string> = {
  draft: "bg-zinc-100 text-zinc-600",
  pending: "bg-blue-100 text-blue-800",
  approved: "bg-emerald-100 text-emerald-800",
  rejected: "bg-red-100 text-red-700",
  applied: "bg-emerald-600 text-white",
  superseded: "bg-zinc-100 text-zinc-500 line-through",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium capitalize ${colors[status] ?? "bg-zinc-100"}`}
    >
      {status}
    </span>
  );
}
