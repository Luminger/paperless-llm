// AUDIT FP-H1: selection is scoped — switching the list identity
// (e.g. taxonomy type) must clear it, or numeric-id overlap silently
// checks the wrong rows.
import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useSelection } from "./selection";

describe("useSelection", () => {
  it("clears itself when the scope key changes", () => {
    const { result, rerender } = renderHook(
      ({ scope }: { scope: string }) => useSelection(scope),
      { initialProps: { scope: "tag" } },
    );
    act(() => result.current.add([3, 7, 12]));
    expect(result.current.selected.size).toBe(3);

    rerender({ scope: "correspondent" });
    expect(result.current.selected.size).toBe(0);

    // Same scope re-render keeps the selection.
    act(() => result.current.add([1]));
    rerender({ scope: "correspondent" });
    expect(result.current.selected.size).toBe(1);
  });
});
