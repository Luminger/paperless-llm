import type { CallTiming } from "../../api";

export function timingLabel(t: CallTiming): string {
  if (t.duration_s == null) return "";
  const parts = [`${t.duration_s.toFixed(1)}s`];
  if (t.tps != null) parts.push(`${t.tps.toFixed(0)} tok/s`);
  if (t.ttft_s != null) parts.push(`ttft ${t.ttft_s.toFixed(2)}s`);
  return parts.join(" · ");
}

export function TimingChip({ t }: { t: CallTiming | null | undefined }) {
  if (!t) return null;
  return (
    <span
      className="text-[10px] text-muted-foreground/60"
      title={`${t.started_at} → ${t.finished_at}, ${t.input_tokens ?? "?"} in / ${t.output_tokens ?? "?"} out tokens`}
    >
      {timingLabel(t)}
    </span>
  );
}
