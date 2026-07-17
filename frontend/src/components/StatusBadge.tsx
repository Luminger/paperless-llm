const labels: Record<string, string> = {
  no_change: "no change needed",
};

const colors: Record<string, string> = {
  draft: "bg-zinc-100 text-zinc-600",
  no_change: "bg-sky-100 text-sky-800",
  pending: "bg-blue-100 text-blue-800",
  rejected: "bg-red-100 text-red-700",
  applied: "bg-emerald-600 text-white",
  superseded: "bg-zinc-100 text-zinc-500 line-through",
  // jobs & sessions
  queued: "bg-zinc-100 text-zinc-600",
  running: "bg-blue-100 text-blue-800",
  completed: "bg-emerald-100 text-emerald-800",
  cancelled: "bg-zinc-100 text-zinc-500",
  failed: "bg-red-100 text-red-700",
  idle: "bg-emerald-100 text-emerald-800",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium capitalize ${colors[status] ?? "bg-zinc-100"}`}
    >
      {labels[status] ?? status}
    </span>
  );
}
