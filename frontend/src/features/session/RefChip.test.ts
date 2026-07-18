import { describe, expect, it } from "vitest";
import { tokenizeRefs } from "./RefChip";

describe("tokenizeRefs", () => {
  it("turns tokens into pllm:// links", () => {
    expect(tokenizeRefs("set [[tag:5]] now")).toBe(
      "set [tag](pllm://tag/5) now",
    );
  });

  // Name echoes after tokens are a PROMPT concern, not a rendering
  // one: the renderer never mangles model prose.
  it("leaves surrounding prose untouched", () => {
    expect(tokenizeRefs('remove [[tag:5]] ("old-stuff-2019")')).toBe(
      'remove [tag](pllm://tag/5) ("old-stuff-2019")',
    );
  });
});
