import { describe, expect, it } from "vitest";
import { tokenizeRefs } from "./RefChip";

describe("tokenizeRefs", () => {
  it("turns tokens into pllm:// links", () => {
    expect(tokenizeRefs("set [[tag:5]] now")).toBe(
      "set [tag](pllm://tag/5) now",
    );
  });

  it("strips the redundant quoted-name parenthetical models add", () => {
    expect(tokenizeRefs('remove [[tag:5]] ("old-stuff-2019") from it')).toBe(
      "remove [tag](pllm://tag/5) from it",
    );
    expect(tokenizeRefs("type [[document_type:2]] (“Brief”)")).toBe(
      "type [document_type](pllm://document_type/2)",
    );
  });

  it("keeps meaningful unquoted parentheticals", () => {
    expect(tokenizeRefs("[[tag:5]] (already assigned)")).toBe(
      "[tag](pllm://tag/5) (already assigned)",
    );
  });
});
