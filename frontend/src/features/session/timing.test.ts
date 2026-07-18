import { describe, expect, it } from "vitest";
import { aggregateTimings } from "./timing";
import type { TranscriptItem } from "../../api";

const item = (timing: TranscriptItem["timing"]): TranscriptItem =>
  ({ role: "agent", content: "x", origin: "chat", timing }) as TranscriptItem;

const t = (start: string, dur: number, out: number) => ({
  started_at: start,
  finished_at: `${start}+`,
  duration_s: dur,
  ttft_s: null,
  input_tokens: 100,
  output_tokens: out,
  tps: out / dur,
});

describe("aggregateTimings", () => {
  it("dedupes items that share one LLM call and totals the rest", () => {
    const shared = t("10:00", 10, 200);
    const agg = aggregateTimings([
      item(shared),
      item(shared), // same call: thinking + text parts share the timing
      item(t("10:01", 5, 100)),
    ]);
    expect(agg).toEqual({ calls: 2, tokens: 300, duration: 15, tps: 20 });
  });

  it("returns null when nothing carries timing", () => {
    expect(aggregateTimings([item(null)])).toBeNull();
  });
});
