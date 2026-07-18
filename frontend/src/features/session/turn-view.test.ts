import { describe, expect, it } from "vitest";
import type { Proposal, Step } from "../../api";
import { deriveTurnView } from "./turn-view";

const step = (over: Partial<Step>): Step =>
  ({
    id: 5,
    session_id: 1,
    kind: "analysis",
    state: "succeeded",
    input: {},
    result: {},
    transcript: [],
    attempts: [],
    attempt_count: 1,
    max_attempts: 3,
    scheduled_at: null,
    error: null,
    lane: "interactive",
    supersedes_id: null,
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    ...over,
  }) as unknown as Step;

const proposal = (over: Partial<Proposal>): Proposal =>
  ({
    id: 9,
    session_id: 1,
    step_id: 5,
    kind: "update_document_metadata",
    status: "pending",
    agent_payload: {},
    user_payload: null,
    revision: 1,
    supersedes_id: null,
    ...over,
  }) as unknown as Proposal;

describe("deriveTurnView", () => {
  it("streaming turns read live items and match proposals by step_id", () => {
    const v = deriveTurnView(
      step({ state: "running" }),
      [proposal({}), proposal({ id: 10, kind: "replace_content" })],
      { tokens: 1, gen: 0, items: [{ role: "thinking", content: "hm" } as never] },
    );
    expect(v.streaming).toBe(true);
    expect(v.items).toHaveLength(1);
    expect(v.mine.map((p) => p.id)).toEqual([9]); // internal kind excluded
    expect(v.summaryIdx).toBe(-1); // never a summary mid-stream
  });

  it("finished turns read the transcript, proposals via result ids, last prose is the summary", () => {
    const v = deriveTurnView(
      step({
        result: { proposal_ids: [9] },
        transcript: [
          { role: "agent", content: "first" },
          { role: "tool", tool_name: "x", content: "" },
          { role: "agent", content: "closing words" },
        ] as never,
      }),
      [proposal({})],
      undefined,
    );
    expect(v.streaming).toBe(false);
    expect(v.mine.map((p) => p.id)).toEqual([9]);
    expect(v.summaryIdx).toBe(2);
  });

  it("AUDIT FS-8: a scheduled retry is NOT streaming — the failed attempt's transcript shows", () => {
    const v = deriveTurnView(
      step({
        state: "pending",
        scheduled_at: "2026-01-01T01:00:00Z",
        transcript: [{ role: "agent", content: "before the crash" }] as never,
      }),
      [],
      { tokens: 0, gen: 0, items: [] },
    );
    expect(v.streaming).toBe(false);
    expect(v.retryScheduled).toBe(true);
    expect(v.items).toHaveLength(1); // transcript, not empty live items
  });
});
