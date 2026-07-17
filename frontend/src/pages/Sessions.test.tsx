import { screen } from "@testing-library/react";
import { vi } from "vitest";
import Sessions from "./Sessions";
import { renderWithProviders } from "../test/utils";
import { api, type Session } from "../api";

vi.mock("../api", () => ({
  api: { listSessions: vi.fn() },
}));
const mocked = vi.mocked(api);

export function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    id: 4,
    agent_kind: "document",
    entity_type: "document",
    entity_id: 7,
    title: "Document #7 analysis",
    status: "idle",
    phase: "done",
    params: { redo_ocr: false },
    error: null,
    created_at: "2026-07-17T10:00:00Z",
    updated_at: "2026-07-17T10:01:00Z",
    proposal_count: 0,
    ...overrides,
  };
}

describe("Sessions", () => {
  beforeEach(() => vi.clearAllMocks());

  it("marks finished runs without proposals explicitly and links to the timeline", async () => {
    mocked.listSessions.mockResolvedValue([makeSession()]);
    renderWithProviders(<Sessions />);

    expect(await screen.findByText("no changes proposed")).toBeInTheDocument();
    const link = screen.getByRole("link") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/sessions/4");
  });

  it("highlights sessions waiting at the OCR gate", async () => {
    mocked.listSessions.mockResolvedValue([
      makeSession({ id: 5, phase: "ocr_review", params: { redo_ocr: true } }),
    ]);
    renderWithProviders(<Sessions />);
    expect(await screen.findByText("OCR review needed")).toBeInTheDocument();
  });

  it("shows failures with their error", async () => {
    mocked.listSessions.mockResolvedValue([
      makeSession({ id: 2, status: "failed", error: "ModelAPIError: Connection error." }),
    ]);
    renderWithProviders(<Sessions />);
    expect(await screen.findByText(/Connection error/)).toBeInTheDocument();
  });

  it("shows proposal counts", async () => {
    mocked.listSessions.mockResolvedValue([makeSession({ proposal_count: 2 })]);
    renderWithProviders(<Sessions />);
    expect(await screen.findByText("2 proposals")).toBeInTheDocument();
  });
});
