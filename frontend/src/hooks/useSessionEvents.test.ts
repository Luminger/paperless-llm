import { reduceProgress, type LiveActivity } from "./useSessionEvents";

const EMPTY: LiveActivity = { tokens: 0, gen: 0, items: [] };

describe("live progress reducer — streaming builds real transcript items", () => {
  it("thinking and text parts accumulate in place", () => {
    let s = reduceProgress(EMPTY, {
      part: 0, part_kind: "thinking", content: "Let me look", tokens: 3,
    });
    s = reduceProgress(s, {
      part: 0, part_kind: "thinking", content: "Let me look at the tags.", tokens: 8,
    });
    s = reduceProgress(s, { part: 1, part_kind: "text", content: "All good.", tokens: 12 });

    expect(s.items).toHaveLength(2);
    expect(s.items[0].role).toBe("thinking");
    expect(s.items[0].content).toBe("Let me look at the tags."); // updated, not appended
    expect(s.items[1].role).toBe("agent");
    expect(s.tokens).toBe(12);
  });

  it("tool call rows get their result attached, rejections marked", () => {
    let s = reduceProgress(EMPTY, { tool: "list_tags", args: "{}" });
    s = reduceProgress(s, { tool: "propose_update_document_metadata", args: '{"document_id":7}' });
    s = reduceProgress(s, {
      tool_done: "propose_update_document_metadata",
      result: "no-op proposal",
      rejected: true,
    });
    s = reduceProgress(s, { tool_done: "list_tags", result: "[…]", rejected: false });

    const [tags, propose] = s.items;
    expect(tags.tool_name).toBe("list_tags");
    expect(tags.tool_result).toBe("[…]");
    expect(propose.tool_rejected).toBe(true);
    expect(propose.tool_result).toContain("rejected:");
  });
});

it("keeps the timeline chronological across model requests (part indices restart)", () => {
  // Request 1: thinking part 0 → tool call → request 2: thinking part 0 again.
  let s = reduceProgress(EMPTY, { part: 0, part_kind: "thinking", content: "planning", tokens: 3 });
  s = reduceProgress(s, { tool: "get_document", args: '{"document_id": 7}' });
  s = reduceProgress(s, { tool_done: "get_document", result: "{}" });
  s = reduceProgress(s, { part: 0, part_kind: "thinking", content: "second thoughts", tokens: 9 });

  // FOUR items in order — request 2's part 0 must NOT overwrite
  // request 1's item above the tool row.
  expect(s.items.map((i) => i.role)).toEqual(["thinking", "tool", "thinking"]);
  expect(s.items[0].content).toBe("planning");
  expect(s.items[2].content).toBe("second thoughts");

  // Streaming updates within request 2 still update in place.
  s = reduceProgress(s, { part: 0, part_kind: "thinking", content: "second thoughts, extended", tokens: 12 });
  expect(s.items).toHaveLength(3);
  expect(s.items[2].content).toBe("second thoughts, extended");
});

it("AUDIT FS-2: tool_done matches FIFO — parallel same-name calls keep their own results", () => {
  let s = reduceProgress(EMPTY, { tool: "get_document", args: '{"document_id": 1}' });
  s = reduceProgress(s, { tool: "get_document", args: '{"document_id": 2}' });
  // Backend serializes execution behind the tool lock: done events
  // arrive in START order.
  s = reduceProgress(s, { tool_done: "get_document", result: "doc one" });
  s = reduceProgress(s, { tool_done: "get_document", result: "doc two" });
  expect(s.items[0].tool_result).toBe("doc one");
  expect(s.items[1].tool_result).toBe("doc two");
});
