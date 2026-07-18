import { reduceProgress, type LiveActivity } from "./useSessionEvents";

const EMPTY: LiveActivity = { tokens: 0, items: [] };

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
