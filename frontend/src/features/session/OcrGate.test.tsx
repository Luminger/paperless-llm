// The OCR gate's decision surface: what the accept button promises,
// the born-digital escape hatch (force_vlm), and the side-by-side
// affordance. The full resolve/keep/redo round-trips live in
// SessionDetail.test.tsx — this file covers the gate-local branches.

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { OcrGateBody } from "./OcrGate";
import { renderWithProviders } from "../../test/utils";
import { api, type Step } from "../../api";

vi.mock("../../api", () => ({
  api: {
    getOcrReview: vi.fn(),
    resolveStep: vi.fn(),
    redoStep: vi.fn(),
  },
}));
const mocked = vi.mocked(api);

function gateStep(over: Partial<Step> = {}): Step {
  return {
    id: 55,
    session_id: 9,
    kind: "ocr",
    state: "awaiting_user",
    lane: "interactive",
    input: {},
    result: { pages: 2 },
    error: null,
    attempts: [],
    attempt_count: 1,
    max_attempts: 3,
    scheduled_at: null,
    supersedes_id: null,
    created_at: "2026-07-17T10:00:00Z",
    started_at: "2026-07-17T10:00:05Z",
    finished_at: null,
    transcript: [],
    ...over,
  };
}

describe("OcrGateBody", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getOcrReview.mockResolvedValue({
      document_id: 7,
      previous_content: "old garbled line",
      ocr_text: "clean OCR line",
      pages: 2,
      timings: [],
    });
    mocked.resolveStep.mockResolvedValue(gateStep({ state: "succeeded" }));
    mocked.redoStep.mockResolvedValue(gateStep({ state: "pending" }));
  });

  it("plain accept promises no edits and sends the OCR text verbatim", async () => {
    renderWithProviders(<OcrGateBody step={gateStep()} />);
    const accept = await screen.findByRole("button", { name: "Accept & continue" });
    expect(accept.textContent).not.toContain("with your fixes");
    await userEvent.click(accept);
    await waitFor(() =>
      expect(mocked.resolveStep).toHaveBeenCalledWith(9, 55, "clean OCR line"),
    );
  });

  it("editing the text upgrades the button to \"with your fixes\"", async () => {
    renderWithProviders(<OcrGateBody step={gateStep()} />);
    await userEvent.click(await screen.findByRole("button", { name: "edit new text" }));
    await userEvent.type(screen.getByLabelText("new content"), " plus a fix");
    expect(
      screen.getByRole("button", { name: /Accept \(with your fixes\) & continue/ }),
    ).toBeInTheDocument();
  });

  it("hides the force-VLM escape hatch when no page came from the text layer", async () => {
    renderWithProviders(<OcrGateBody step={gateStep()} />);
    await userEvent.click(await screen.findByText(/Re-run it with instructions/));
    expect(
      screen.queryByLabelText(/Ignore the PDF's embedded text/),
    ).not.toBeInTheDocument();
  });

  it("born-digital runs offer force-VLM; the redo carries the flag", async () => {
    renderWithProviders(
      <OcrGateBody step={gateStep({ result: { pages: 2, native_pages: 2 } })} />,
    );
    await userEvent.click(await screen.findByText(/Re-run it with instructions/));
    await userEvent.click(
      screen.getByLabelText(/Ignore the PDF's embedded text — OCR every page/),
    );
    await userEvent.click(screen.getByRole("button", { name: "Re-run OCR" }));
    await waitFor(() =>
      expect(mocked.redoStep).toHaveBeenCalledWith(9, 55, {
        instructions: undefined,
        force_vlm: true,
      }),
    );
  });

  it("a redo without any tweaks sends neither flag nor instructions", async () => {
    renderWithProviders(<OcrGateBody step={gateStep()} />);
    await userEvent.click(await screen.findByText(/Re-run it with instructions/));
    await userEvent.click(screen.getByRole("button", { name: "Re-run OCR" }));
    await waitFor(() =>
      expect(mocked.redoStep).toHaveBeenCalledWith(9, 55, {
        instructions: undefined,
        force_vlm: undefined,
      }),
    );
  });

  it("offers the pinned-panel comparison right where the judgment happens", async () => {
    renderWithProviders(<OcrGateBody step={gateStep()} />);
    const compare = await screen.findByRole("button", {
      name: /Compare against the pages/,
    });
    await userEvent.click(compare);
    // The link is only an offer while the panel is closed.
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /Compare against the pages/ }),
      ).not.toBeInTheDocument(),
    );
  });

  it("a failing resolve keeps the gate and shows the error", async () => {
    mocked.resolveStep.mockRejectedValue(new Error("session is archived"));
    renderWithProviders(<OcrGateBody step={gateStep()} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Keep existing content/ }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("session is archived");
    // Both decisions remain available.
    expect(screen.getByRole("button", { name: "Accept & continue" })).toBeEnabled();
  });
});
