// Finished OCR steps must say what was DECIDED — accepted content via
// the journaled proposal, kept-existing, or the born-digital
// auto-resolution — and the footer/batches must account for pages that
// never hit the vision model. (Streaming/batch progress is covered in
// StepCard.test.tsx.)

import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import { StepCard } from "./StepCard";
import type { Proposal, Step } from "../../api";

vi.mock("../../api", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../../api")>();
  return { ...orig, api: new Proxy({}, { get: () => vi.fn() }) };
});

const base = {
  id: 5,
  session_id: 1,
  kind: "ocr",
  state: "succeeded",
  lane: "interactive",
  input: {},
  result: {},
  error: null,
  attempts: [],
  attempt_count: 1,
  max_attempts: 3,
  scheduled_at: null,
  supersedes_id: null,
  created_at: "2026-07-18T10:00:00Z",
  started_at: "2026-07-18T10:00:01Z",
  finished_at: "2026-07-18T10:02:00Z",
  transcript: [],
} as unknown as Step;

const ocrStep = (result: object, over: object = {}) =>
  ({ ...base, result, ...over }) as unknown as Step;

const renderStep = (step: Step, proposals: Proposal[] = []) =>
  renderWithProviders(
    <StepCard
      step={step}
      proposals={proposals}
      live={undefined}
      onChanged={() => {}}
      archived={false}
    />,
  );

describe("StepCard — finished OCR outcomes", () => {
  it("born-digital documents explain the auto-resolved gate", () => {
    renderStep(
      ocrStep({
        pages: 3,
        native_pages: 3,
        duration_s: 0.4,
        resolution: "auto_native",
        text: "same text",
        previous_content: "same text",
        batches: [{ pages: "1-3", native: true, count: 3 }],
      }),
    );
    expect(
      screen.getByText("born-digital — embedded text verified, gate auto-resolved"),
    ).toBeInTheDocument();
    // The batch row names the source instead of fake call metrics.
    expect(
      screen.getByText("embedded text layer (born-digital) — no OCR call"),
    ).toBeInTheDocument();
    // The footer counts the born-digital pages.
    expect(screen.getByText(/3 pages .* 3 born-digital/)).toBeInTheDocument();
  });

  it("a kept-existing decision is stated and the diff stays inspectable", async () => {
    renderStep(
      ocrStep({
        pages: 1,
        duration_s: 12.5,
        resolution: "kept_existing",
        text: "what OCR produced",
        previous_content: "the content that was kept",
      }),
    );
    expect(screen.getByText("existing content kept")).toBeInTheDocument();
    // The rejected OCR output remains reviewable, read-only (the diff
    // renders asynchronously).
    await waitFor(() =>
      expect(document.body.textContent).toContain("what OCR produced"),
    );
    expect(document.body.textContent).toContain("the content that was kept");
    expect(
      screen.queryByRole("button", { name: "edit new text" }),
    ).not.toBeInTheDocument();
  });

  it("accepted content rides the journaled proposal: badge + decided-by", () => {
    const contentProposal = {
      id: 41,
      session_id: 1,
      step_id: 5,
      kind: "replace_content",
      revision: 1,
      supersedes_id: null,
      agent_payload: { kind: "replace_content", document_id: 7 },
      user_payload: null,
      base_snapshot: null,
      status: "applied",
      entity_type: "document",
      entity_id: 7,
      created_at: "2026-07-18T10:02:00Z",
      updated_at: "2026-07-18T10:02:00Z",
      applied: true,
      applied_by: "simon",
      applied_at: "2026-07-18T10:02:30Z",
      reverted: false,
    } as unknown as Proposal;
    renderStep(
      ocrStep({
        pages: 1,
        duration_s: 12.5,
        resolution: "accepted",
        text: "new content",
        previous_content: "old content",
      }),
      [contentProposal],
    );
    expect(screen.getByText("applied")).toBeInTheDocument();
    expect(screen.getByText(/accepted by you/)).toBeInTheDocument();
    // The internal kind never renders as a proposal card.
    expect(screen.queryByText("Proposal")).not.toBeInTheDocument();
  });

  it("user-edited acceptances are credited as edited", () => {
    const edited = {
      id: 42,
      session_id: 1,
      step_id: 5,
      kind: "replace_content",
      revision: 1,
      supersedes_id: null,
      agent_payload: { kind: "replace_content", document_id: 7 },
      user_payload: { content: "fixed by hand" },
      base_snapshot: null,
      status: "applied",
      entity_type: "document",
      entity_id: 7,
      created_at: "2026-07-18T10:02:00Z",
      updated_at: "2026-07-18T10:02:00Z",
      applied: true,
      applied_by: "simon",
      applied_at: null,
      reverted: false,
    } as unknown as Proposal;
    renderStep(
      ocrStep({ pages: 1, duration_s: 3, resolution: "accepted", text: "t" }),
      [edited],
    );
    expect(screen.getByText("accepted by you (edited)")).toBeInTheDocument();
  });
});
