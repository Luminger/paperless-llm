import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import Sessions from "./Sessions";
import { renderWithProviders } from "../test/utils";
import { api, type Session, type SessionPage } from "../api";

vi.mock("../api", () => ({
  api: {
    listSessions: vi.fn(),
    getStats: vi.fn(),
    archiveSession: vi.fn(),
    unarchiveSession: vi.fn(),
  },
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
    archived_at: null,
    created_at: "2026-07-17T10:00:00Z",
    updated_at: "2026-07-17T10:01:00Z",
    proposal_count: 0,
    ...overrides,
  };
}

function page(results: Session[], count = results.length): SessionPage {
  return { count, page: 1, page_size: 5, results };
}

describe("Sessions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getStats.mockResolvedValue({
      pending_proposals: 1,
      active_sessions: 0,
      queue_pending: {},
      active_jobs: 0,
    });
  });

  it("marks finished runs without proposals explicitly and links to the timeline", async () => {
    mocked.listSessions.mockResolvedValue(page([makeSession()]));
    renderWithProviders(<Sessions />);

    expect(await screen.findByText("no changes proposed")).toBeInTheDocument();
    const links = screen.getAllByRole("link") as HTMLAnchorElement[];
    expect(links.some((l) => l.getAttribute("href") === "/sessions/4")).toBe(true);
  });

  it("highlights sessions waiting at the OCR gate", async () => {
    mocked.listSessions.mockResolvedValue(
      page([makeSession({ id: 5, phase: "ocr_review", params: { redo_ocr: true } })]),
    );
    renderWithProviders(<Sessions />);
    expect(await screen.findByText("OCR review needed")).toBeInTheDocument();
  });

  it("shows failures with their error", async () => {
    mocked.listSessions.mockResolvedValue(
      page([makeSession({ id: 2, status: "failed", error: "ModelAPIError: Connection error." })]),
    );
    renderWithProviders(<Sessions />);
    expect(await screen.findByText(/Connection error/)).toBeInTheDocument();
  });

  it("paginates: 5 per page with a generic pager", async () => {
    const sessions = Array.from({ length: 5 }, (_, i) => makeSession({ id: i + 1 }));
    mocked.listSessions.mockResolvedValue(page(sessions, 12));
    renderWithProviders(<Sessions />);

    expect(await screen.findByText(/page 1 of 3 · 12 total/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "next ›" }));
    await waitFor(() =>
      expect(mocked.listSessions).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2, page_size: 5, archived: false }),
      ),
    );
  });

  it("archives a session from the list", async () => {
    mocked.listSessions.mockResolvedValue(page([makeSession()]));
    mocked.archiveSession.mockResolvedValue(makeSession({ archived_at: "2026-07-17T12:00:00Z" }));
    renderWithProviders(<Sessions />);

    await userEvent.click(await screen.findByRole("button", { name: "Archive" }));
    await waitFor(() => expect(mocked.archiveSession).toHaveBeenCalledWith(4));
  });

  it("archived sessions live in a collapsed section, unarchivable", async () => {
    mocked.listSessions.mockImplementation(async (f) =>
      f?.archived
        ? page([makeSession({ id: 9, archived_at: "2026-07-17T12:00:00Z" })])
        : page([]),
    );
    mocked.unarchiveSession.mockResolvedValue(makeSession({ id: 9 }));
    renderWithProviders(<Sessions />);

    // Collapsed by default — the archived list is not fetched yet.
    expect(screen.queryByText("Unarchive")).not.toBeInTheDocument();
    await userEvent.click(await screen.findByText("Archived sessions"));
    await userEvent.click(await screen.findByRole("button", { name: "Unarchive" }));
    await waitFor(() => expect(mocked.unarchiveSession).toHaveBeenCalledWith(9));
  });
});
