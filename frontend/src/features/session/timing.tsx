import type { CallTiming, TranscriptItem } from "../../api";
import { Tip } from "@/components/app/Tip";

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
    <Tip
      content={`${t.started_at} → ${t.finished_at}, ${t.input_tokens ?? "?"} in / ${t.output_tokens ?? "?"} out tokens`}
    >
      <span className="text-[10px] text-muted-foreground/60" tabIndex={0}>
        {timingLabel(t)}
      </span>
    </Tip>
  );
}

/** Whole-turn totals: items sharing an LLM call share one timing
 * object, so calls are deduped by their start/end stamps. */
export function aggregateTimings(items: TranscriptItem[]): {
  calls: number;
  tokens: number;
  duration: number;
  tps: number | null;
} | null {
  const seen = new Set<string>();
  let calls = 0;
  let tokens = 0;
  let duration = 0;
  for (const it of items) {
    const t = it.timing;
    if (!t || t.duration_s == null) continue;
    const key = `${t.started_at}|${t.finished_at}`;
    if (seen.has(key)) continue;
    seen.add(key);
    calls += 1;
    duration += t.duration_s;
    tokens += t.output_tokens ?? 0;
  }
  if (calls === 0) return null;
  return {
    calls,
    tokens,
    duration,
    tps: tokens > 0 && duration > 0 ? tokens / duration : null,
  };
}

const fmtTokens = (n: number) =>
  n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : String(n);

/** "3 calls · 812 tokens · 41 tok/s · 24.1s" for a turn's footer. */
export function StepTimingSummary({ items }: { items: TranscriptItem[] }) {
  const agg = aggregateTimings(items);
  if (!agg) return null;
  const parts = [
    `${agg.calls} LLM call${agg.calls !== 1 ? "s" : ""}`,
    ...(agg.tokens > 0 ? [`${fmtTokens(agg.tokens)} tokens`] : []),
    ...(agg.tps != null ? [`${agg.tps.toFixed(0)} tok/s`] : []),
    `${agg.duration.toFixed(1)}s`,
  ];
  return (
    <span className="text-xs text-muted-foreground/70">{parts.join(" · ")}</span>
  );
}
