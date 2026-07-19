// Streaming builds the REAL UI piece by piece: the live tail is an
// open "The agent's work…" panel; when a proposal pops in, that panel
// seals shut and the proposal card renders in place.

import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import { StepCard } from "./StepCard";
import type { Proposal, Step } from "../../api";
import type { LiveTranscriptItem } from "../../hooks/useSessionEvents";

vi.mock("../../api", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../../api")>();
  return { ...orig, api: new Proxy({}, { get: () => vi.fn() }) };
});

const step = {
  id: 5,
  session_id: 1,
  kind: "analysis",
  state: "running",
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
  finished_at: null,
  transcript: [],
} as unknown as Step;

const liveItem = (over: object): LiveTranscriptItem => ({
  role: "agent",
  content: "",
  origin: "chat",
  tool_name: null,
  tool_args: null,
  tool_result: null,
  tool_result_full: null,
  tool_rejected: false,
  proposal_id: null,
  timing: null,
  ts: null,
  ...over,
}) as LiveTranscriptItem;

describe("StepCard streaming", () => {
  it("a streaming chat turn shows the user's message from the first moment", () => {
    renderWithProviders(
      <StepCard
        step={
          {
            ...step,
            kind: "chat",
            input: { content: "also check the date" },
          } as unknown as Step
        }
        proposals={[]}
        live={{ tokens: 5, gen: 0, items: [] }}
        onChanged={() => {}}
        archived={false}
        turn={2}
      />,
    );
    // Not "magically after streaming": the box is there immediately.
    expect(screen.getByText("also check the date")).toBeInTheDocument();
    expect(screen.getByText("Turn 2")).toBeInTheDocument();
  });

  it("the whole turn folds via the header, independent of inner folds", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    renderWithProviders(
      <StepCard
        step={{ ...step, state: "succeeded" } as unknown as Step}
        proposals={[]}
        live={undefined}
        onChanged={() => {}}
        archived={false}
        turn={1}
      />,
    );
    expect(screen.getByText("No changes proposed.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Initial analysis/ }));
    expect(screen.queryByText("No changes proposed.")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Initial analysis/ }));
    expect(screen.getByText("No changes proposed.")).toBeInTheDocument();
  });

  it("live tail is an open work panel", () => {
    renderWithProviders(
      <StepCard
        step={step}
        proposals={[]}
        live={{
          tokens: 42,
          gen: 0,
          items: [
            liveItem({ role: "thinking", content: "checking the taxonomy" }),
            liveItem({ role: "tool", tool_name: "list_tags", tool_args: {} }),
          ],
        }}
        onChanged={() => {}}
        archived={false}
        turn={1}
      />,
    );
    expect(screen.getByText("The agent's work…")).toBeInTheDocument();
    // Open: its rows are visible without clicking.
    expect(screen.getByText(/checking the taxonomy/)).toBeInTheDocument();
    expect(screen.getByText("list_tags")).toBeInTheDocument();
    expect(screen.getByText(/generating… 42 tokens/)).toBeInTheDocument();
    expect(screen.getByText("Initial analysis")).toBeInTheDocument();
  });

  it("a streamed proposal seals the work panel and pops the real card", () => {
    const proposal = {
      id: 31,
      session_id: 1,
      step_id: 5,
      kind: "create_entity",
      revision: 1,
      supersedes_id: null,
      agent_payload: { entity_type: "tag", name: "Rechnung" },
      user_payload: null,
      base_snapshot: null,
      status: "draft",
      entity_type: "tag",
      entity_id: null,
      created_at: "2026-07-18T10:00:02Z",
      updated_at: "2026-07-18T10:00:02Z",
      applied: false,
      reverted: false,
      applied_by: null,
    } as unknown as Proposal;
    renderWithProviders(
      <StepCard
        step={step}
        proposals={[proposal]}
        live={{
          tokens: 90,
          gen: 0,
          items: [
            liveItem({ role: "thinking", content: "needs a new tag" }),
            liveItem({
              role: "tool",
              tool_name: "propose_create_entity",
              tool_args: {},
              tool_result: "Proposal [[proposal:31]] recorded",
              proposal_id: 31,
            }),
            liveItem({ role: "thinking", content: "now the metadata" }),
          ],
        }}
        onChanged={() => {}}
        archived={false}
        turn={1}
      />,
    );
    // The proposal card is IN the streaming timeline…
    expect(screen.getByText("Proposal")).toBeInTheDocument();
    expect(screen.getByText("create tag")).toBeInTheDocument();
    // …the pre-proposal work sealed shut (closed panel)…
    expect(screen.getByText("The agent's work")).toBeInTheDocument();
    // …and the post-proposal tail is open and growing.
    expect(screen.getByText("The agent's work…")).toBeInTheDocument();
    expect(screen.getByText(/now the metadata/)).toBeInTheDocument();
  });
});

describe("OCR progress & statistics", () => {
  const ocrStep = (over: object) =>
    ({
      ...step,
      kind: "ocr",
      ...over,
    }) as unknown as Step;

  it("a running OCR step shows batched pages, per-batch stats and the latest text", () => {
    renderWithProviders(
      <StepCard
        step={ocrStep({
          state: "running",
          result: {
            progress: {
              total_pages: 6,
              done_pages: 4,
              total_batches: 3,
              batches: [
                { pages: "1-2", duration_s: 41.2, output_tokens: 900, tps: 22.1 },
                {
                  pages: "3-4",
                  duration_s: 39.8,
                  output_tokens: 850,
                  tps: 21.4,
                  rotated: [3],
                  text: "## Seite 3\nHallo",
                },
              ],
            },
          },
        })}
        proposals={[]}
        live={undefined}
        onChanged={() => {}}
        archived={false}
      />,
    );
    expect(screen.getByText(/Transcribing — page 4 of 6/)).toBeInTheDocument();
    expect(screen.getByText(/batch 2 of 3/)).toBeInTheDocument();
    expect(screen.getByText("pages 1-2")).toBeInTheDocument();
    expect(screen.getByText(/41.2s · 900 tok · 22.1 tok\/s/)).toBeInTheDocument();
    // the flipped page is called out
    expect(screen.getByText(/auto-rotated p\. 3/)).toBeInTheDocument();
    // the latest batch's returned text is inspectable
    expect(
      screen.getByText(/latest returned text \(pages 3-4\)/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Hallo/)).toBeInTheDocument();
  });

  it("a finished OCR step keeps per-batch metrics and aggregates the footer", () => {
    renderWithProviders(
      <StepCard
        step={ocrStep({
          state: "succeeded",
          finished_at: "2026-07-18T10:03:00Z",
          result: {
            pages: 4,
            dpi: 150,
            duration_s: 81.0,
            resolution: "accepted",
            text: "content",
            previous_content: "old",
            batches: [
              { pages: "1-2", duration_s: 41.2, output_tokens: 900, tps: 22.1 },
              { pages: "3-4", duration_s: 39.8, output_tokens: 850, tps: 20.1, rotated: [4] },
            ],
          },
        })}
        proposals={[]}
        live={undefined}
        onChanged={() => {}}
        archived={false}
      />,
    );
    expect(screen.getByText(/2 OCR calls — expand for per-batch metrics/)).toBeInTheDocument();
    // footer aggregates: pages, DPI, time, tokens, avg tps, rotations
    expect(
      screen.getByText(/4 pages · 150 DPI · 81s · 1,750 tok · 21.1 tok\/s · 1 page auto-rotated/),
    ).toBeInTheDocument();
  });
});
