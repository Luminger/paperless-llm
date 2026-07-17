// One page scaffold: title left, actions right, optional filter row
// below. Every page uses this — no hand-rolled header layouts.

export function PageHeader({
  title,
  actions,
  filters,
}: {
  title: React.ReactNode;
  actions?: React.ReactNode;
  filters?: React.ReactNode;
}) {
  return (
    <header className="mb-4 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {filters && <div className="flex flex-wrap items-center gap-2">{filters}</div>}
    </header>
  );
}
