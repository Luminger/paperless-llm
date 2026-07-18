import { describe, expect, it } from "vitest";
import { remarkRefs } from "./RefChip";

// Run the plugin on a hand-built mdast fragment (what remark hands it).
function run(tree: object) {
  (remarkRefs() as (t: unknown) => void)(tree);
  return tree;
}

describe("remarkRefs (AUDIT FS-9: AST-level, code stays literal)", () => {
  it("turns text tokens into pllm:// links", () => {
    const tree = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [{ type: "text", value: "set [[tag:5]] now" }],
        },
      ],
    };
    run(tree);
    const para = (tree.children as never[])[0] as {
      children: { type: string; url?: string; value?: string }[];
    };
    expect(para.children.map((c) => c.type)).toEqual(["text", "link", "text"]);
    expect(para.children[1].url).toBe("pllm://tag/5");
    expect(para.children[0].value).toBe("set ");
    expect(para.children[2].value).toBe(" now");
  });

  it("leaves tokens inside code spans and fences literal", () => {
    const tree = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [{ type: "inlineCode", value: "[[tag:5]]" }],
        },
        { type: "code", value: "remove [[tag:7]]" },
      ],
    };
    run(tree);
    const para = (tree.children as never[])[0] as { children: { type: string; value: string }[] };
    expect(para.children[0]).toEqual({ type: "inlineCode", value: "[[tag:5]]" });
    expect((tree.children as { value?: string }[])[1].value).toBe("remove [[tag:7]]");
  });

  it("untokened text is untouched (no mangling)", () => {
    const tree = {
      type: "root",
      children: [
        { type: "paragraph", children: [{ type: "text", value: "plain [not a token]" }] },
      ],
    };
    run(tree);
    const para = (tree.children as never[])[0] as { children: { value: string }[] };
    expect(para.children).toHaveLength(1);
    expect(para.children[0].value).toBe("plain [not a token]");
  });
});
