// The job-scheduling modal: options are explicit, nothing runs until
// Start, and NO state leaks from one opening into the next.

import { useState } from "react";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { renderWithProviders } from "../../test/utils";
import { StartJobDialog } from "./StartJobDialog";

function renderDialog(props: Partial<Parameters<typeof StartJobDialog>[0]> = {}) {
  const onStart = vi.fn();
  renderWithProviders(
    <StartJobDialog
      open
      onOpenChange={() => {}}
      title="Analyze the inbox"
      description="12 documents"
      onStart={onStart}
      {...props}
    />,
  );
  return onStart;
}

describe("StartJobDialog", () => {
  it("defaults to review policy with no instructions", async () => {
    const onStart = renderDialog();
    expect(screen.getByText("Analyze the inbox")).toBeInTheDocument();
    expect(screen.getByText("12 documents")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start job" }));
    expect(onStart).toHaveBeenCalledWith({
      apply_policy: "review",
      instructions: undefined,
    });
  });

  it("auto-apply flips the policy; instructions are trimmed", async () => {
    const onStart = renderDialog();
    await userEvent.click(screen.getByLabelText(/auto-apply proposals/));
    await userEvent.type(
      screen.getByLabelText("job instructions"),
      "  prefer German titles  ",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start job" }));
    expect(onStart).toHaveBeenCalledWith({
      apply_policy: "auto",
      instructions: "prefer German titles",
    });
  });

  it("hides the re-OCR checkbox unless the job scope offers it", () => {
    renderDialog();
    expect(screen.queryByLabelText(/re-do ocr first/i)).not.toBeInTheDocument();
  });

  it("redoOcrOption: checked sends redo_ocr, unchecked omits it", async () => {
    const onStart = renderDialog({ redoOcrOption: true });
    // Unchecked: the flag stays falsy (backend default).
    await userEvent.click(screen.getByRole("button", { name: "Start job" }));
    expect(onStart.mock.calls[0][0].redo_ocr).toBeUndefined();

    await userEvent.click(screen.getByLabelText(/re-do ocr first/i));
    await userEvent.click(screen.getByRole("button", { name: "Start job" }));
    expect(onStart.mock.calls[1][0]).toMatchObject({
      apply_policy: "review",
      redo_ocr: true,
    });
  });

  it("supports custom auto-apply and start labels (OCR-only jobs)", () => {
    renderDialog({
      autoLabel: "auto-apply the new text",
      startLabel: "Start re-OCR",
    });
    expect(screen.getByLabelText("auto-apply the new text")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start re-OCR" })).toBeInTheDocument();
  });

  it("Cancel closes without starting anything", async () => {
    const onOpenChange = vi.fn();
    const onStart = vi.fn();
    renderWithProviders(
      <StartJobDialog
        open
        onOpenChange={onOpenChange}
        title="Analyze the inbox"
        description="d"
        onStart={onStart}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onStart).not.toHaveBeenCalled();
  });

  it("resets checkboxes and instructions on every re-open", async () => {
    // Dashboard callers keep the dialog mounted — stale state must not
    // leak into the next opening.
    function Harness() {
      const [open, setOpen] = useState(true);
      return (
        <>
          <button onClick={() => setOpen(true)}>reopen</button>
          <StartJobDialog
            open={open}
            onOpenChange={setOpen}
            title="Analyze the inbox"
            description="d"
            onStart={() => {}}
            redoOcrOption
          />
        </>
      );
    }
    renderWithProviders(<Harness />);
    await userEvent.click(screen.getByLabelText(/auto-apply proposals/));
    await userEvent.click(screen.getByLabelText(/re-do ocr first/i));
    await userEvent.type(screen.getByLabelText("job instructions"), "old text");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await userEvent.click(screen.getByRole("button", { name: "reopen" }));
    expect(screen.getByLabelText(/auto-apply proposals/)).not.toBeChecked();
    expect(screen.getByLabelText(/re-do ocr first/i)).not.toBeChecked();
    expect(screen.getByLabelText("job instructions")).toHaveValue("");
  });
});
