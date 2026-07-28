import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import Dashboard from "./Dashboard";
import { renderWithProviders } from "../test/utils";
import { api, type Session, type SessionPage } from "../api";

vi.mock("../api", () => ({
  api: {
    listSessions: vi.fn(),
    getStats: vi.fn(),
    getCorpus: vi.fn(),
    getInbox: vi.fn(),
    createJob: vi.fn(),
    listCorrespondents: vi.fn(),
    archiveSession: vi.fn(),
    unarchiveSession: vi.fn(),
    cancelSession: vi.fn(),
  },
}));
const mocked = vi.mocked(api);

export function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    id: 4,
    agent_kind: "document",
    entity_type: "document",
    entity_id: 7,
    entity_name: "",
    title: "Document #7 analysis",
    status: "idle",
    phase: "done",
    params: { redo_ocr: false },
    error: null,
    archived_at: null,
    created_at: "2026-07-17T10:00:00Z",
    updated_at: "2026-07-17T10:01:00Z",
    proposal_count: 0,
    pending_proposal_count: 0,
    applied_proposal_count: 0,
    ...overrides,
  };
}

function page(results: Session[], count = results.length): SessionPage {
  return { count, page: 1, page_size: 5, results };
}

describe("Dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getCorpus.mockResolvedValue({ total: 2400, processed: 118 });
    mocked.getInbox.mockResolvedValue({ count: 0, page_size: 25, all: null, results: [] });
    mocked.listCorrespondents.mockResolvedValue([]);
    mocked.getStats.mockResolvedValue({
      pending_proposals: 1,
      active_sessions: 0,
      queue_pending: {},
      active_jobs: 0,
      lifetime: { ocr_runs: 7, llm_output_tokens: 123456, llm_input_tokens: 9 },
    });
  });

  it("marks finished runs without proposals explicitly and links to the timeline", async () => {
    mocked.listSessions.mockResolvedValue(page([makeSession()]));
    renderWithProviders(<Dashboard />);

    expect(await screen.findByText("no changes proposed")).toBeInTheDocument();
    const links = screen.getAllByRole("link") as HTMLAnchorElement[];
    expect(links.some((l) => l.getAttribute("href") === "/sessions/4")).toBe(true);
  });

  it("highlights sessions waiting at the OCR gate", async () => {
    mocked.listSessions.mockResolvedValue(
      page([makeSession({ id: 5, phase: "ocr_review", params: { redo_ocr: true } })]),
    );
    renderWithProviders(<Dashboard />);
    expect(await screen.findByText("OCR review needed")).toBeInTheDocument();
  });

  it("running sessions get a Stop button that cancels the run", async () => {
    const user = userEvent.setup();
    mocked.cancelSession.mockResolvedValue(makeSession({ status: "idle" }));
    mocked.listSessions.mockResolvedValue(
      page([
        makeSession({ id: 4, status: "running", phase: "analyzing" }),
        makeSession({ id: 5, status: "idle", phase: "done" }),
      ]),
    );
    renderWithProviders(<Dashboard />);
    // Exactly one Stop — only the running row gets it.
    const stops = await screen.findAllByRole("button", { name: /Stop/ });
    expect(stops).toHaveLength(1);
    await user.click(stops[0]);
    expect(mocked.cancelSession).toHaveBeenCalledWith(4);
  });

  it("queued sessions (pending work, not yet claimed) are stoppable too", async () => {
    mocked.listSessions.mockResolvedValue(
      page([makeSession({ id: 6, status: "idle", phase: "queued" })]),
    );
    renderWithProviders(<Dashboard />);
    expect(await screen.findByRole("button", { name: /Stop/ })).toBeInTheDocument();
  });

  it("failed sessions show an Error badge, never the error text", async () => {
    mocked.listSessions.mockResolvedValue(
      page([makeSession({ id: 2, status: "failed", error: "ModelAPIError: Connection error." })]),
    );
    renderWithProviders(<Dashboard />);
    expect(await screen.findByText("Error")).toBeInTheDocument();
    expect(screen.queryByText(/Connection error/)).toBeNull();
  });

  it("paginates: 5 per page with a generic pager", async () => {
    const sessions = Array.from({ length: 5 }, (_, i) => makeSession({ id: i + 1 }));
    mocked.listSessions.mockResolvedValue(page(sessions, 12));
    renderWithProviders(<Dashboard />);

    expect(await screen.findByText(/12 sessions/)).toBeInTheDocument();
    // Framework pagination: numbered links + prev/next.
    expect(screen.getByRole("link", { name: "3" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "Go to next page" }));
    await waitFor(() =>
      expect(mocked.listSessions).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2, page_size: 5, archived: false }),
      ),
    );
  });

  it("archives a session from the list", async () => {
    mocked.listSessions.mockResolvedValue(page([makeSession()]));
    mocked.archiveSession.mockResolvedValue(makeSession({ archived_at: "2026-07-17T12:00:00Z" }));
    renderWithProviders(<Dashboard />);

    await userEvent.click(await screen.findByRole("button", { name: "Archive" }));
    await waitFor(() => expect(mocked.archiveSession).toHaveBeenCalledWith(4));
  });

  it("shows lifetime stats and hides the archived section", async () => {
    mocked.listSessions.mockResolvedValue(page([]));
    renderWithProviders(<Dashboard />);

    expect(await screen.findByText("OCR runs (lifetime)")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("123k")).toBeInTheDocument();
    expect(screen.getByText("LLM tokens generated (lifetime)")).toBeInTheDocument();
    expect(await screen.findByText("Nothing needs attention.")).toBeInTheDocument();
    expect(screen.queryByText("Archived sessions")).not.toBeInTheDocument();
  });

  it("requests only unfinished sessions", async () => {
    mocked.listSessions.mockResolvedValue(page([]));
    renderWithProviders(<Dashboard />);
    await waitFor(() =>
      expect(mocked.listSessions).toHaveBeenCalledWith(
        expect.objectContaining({ unfinished: true, archived: false, page_size: 5 }),
      ),
    );
  });
});

describe("Dashboard corpus block", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getInbox.mockResolvedValue({ count: 0, page_size: 25, all: null, results: [] });
    mocked.listCorrespondents.mockResolvedValue([]);
    mocked.getStats.mockResolvedValue({
      pending_proposals: 0,
      active_sessions: 0,
      queue_pending: {},
      active_jobs: 0,
      lifetime: {},
    });
    mocked.listSessions.mockResolvedValue({ count: 0, page: 1, page_size: 5, results: [] });
  });

  it("shows curation progress and starts the next batch", async () => {
    mocked.getCorpus.mockResolvedValue({ total: 2400, processed: 118 });
    mocked.getInbox.mockResolvedValue({ count: 0, page_size: 25, all: null, results: [] });
    mocked.listCorrespondents.mockResolvedValue([]);
    mocked.createJob.mockResolvedValue({ id: 9 } as never);
    renderWithProviders(<Dashboard />);
    expect(
      await screen.findByText(/118 of 2,400 analyzed/),
    ).toBeInTheDocument();
    // Scheduling goes through a modal — nothing runs on first click.
    await userEvent.click(screen.getByRole("button", { name: /analyze next batch/i }));
    expect(mocked.createJob).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByRole("button", { name: /start job/i }));
    await waitFor(() =>
      expect(mocked.createJob).toHaveBeenCalledWith({
        next_batch: 10,
        apply_policy: "review",
        instructions: undefined,
      }),
    );
  });

  it("declares a fully analyzed corpus", async () => {
    mocked.getCorpus.mockResolvedValue({ total: 13, processed: 13 });
    renderWithProviders(<Dashboard />);
    expect(
      await screen.findByText(/every document has been analyzed/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /analyze next batch/i })).toBeNull();
  });
});

describe("Dashboard inbox block", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getStats.mockResolvedValue({
      pending_proposals: 0,
      active_sessions: 0,
      queue_pending: {},
      active_jobs: 0,
      lifetime: {},
    });
    mocked.listSessions.mockResolvedValue({ count: 0, page: 1, page_size: 5, results: [] });
    mocked.getCorpus.mockResolvedValue({ total: 13, processed: 13 });
    mocked.listCorrespondents.mockResolvedValue([{ id: 2, name: "Kraxi" } as never]);
  });

  it("lists waiting documents and starts the inbox job", async () => {
    mocked.getInbox.mockResolvedValue({
      count: 12,
      page_size: 25,
      all: null,
      results: [
        {
          id: 7,
          title: "scan_0234",
          tags: [9],
          correspondent: 2,
          document_type: null,
          storage_path: null,
          created: "2026-07-01",
          content: null,
        } as never,
      ],
    });
    mocked.createJob.mockResolvedValue({ id: 4 } as never);
    renderWithProviders(<Dashboard />);
    expect(await screen.findByText("Inbox")).toBeInTheDocument();
    expect(screen.getByText(/12 documents waiting/)).toBeInTheDocument();
    expect(screen.getByText("scan_0234")).toBeInTheDocument();
    expect(await screen.findByText("Kraxi")).toBeInTheDocument();
    expect(screen.getByText(/and 11 more/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /analyze the inbox/i }));
    expect(mocked.createJob).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByRole("button", { name: /start job/i }));
    await waitFor(() =>
      expect(mocked.createJob).toHaveBeenCalledWith({
        inbox: true,
        apply_policy: "review",
        instructions: undefined,
      }),
    );
  });

  const inboxOf12 = {
    count: 12,
    page_size: 25,
    all: null,
    results: [],
  };

  it("analyze dialog carries a re-OCR flag", async () => {
    mocked.getInbox.mockResolvedValue(inboxOf12 as never);
    mocked.createJob.mockResolvedValue({ id: 4 } as never);
    renderWithProviders(<Dashboard />);
    await screen.findByText(/12 documents waiting/);
    await userEvent.click(screen.getByRole("button", { name: /analyze the inbox/i }));
    await userEvent.click(
      await screen.findByRole("checkbox", { name: /re-do ocr first/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: /start job/i }));
    await waitFor(() =>
      expect(mocked.createJob).toHaveBeenCalledWith({
        inbox: true,
        redo_ocr: true,
        apply_policy: "review",
        instructions: undefined,
      }),
    );
  });

  it("dedicated Re-OCR button starts an OCR-only job via its own dialog", async () => {
    mocked.getInbox.mockResolvedValue(inboxOf12 as never);
    mocked.createJob.mockResolvedValue({ id: 5 } as never);
    renderWithProviders(<Dashboard />);
    await screen.findByText(/12 documents waiting/);
    await userEvent.click(screen.getByRole("button", { name: /^re-ocr/i }));
    // OCR-only: no analysis — the dialog says so and nothing runs yet.
    expect(await screen.findByText(/no metadata analysis/i)).toBeInTheDocument();
    // Instructions steer the transcription, not the agent.
    expect(
      screen.getByPlaceholderText(/Optional OCR instructions/),
    ).toBeInTheDocument();
    expect(mocked.createJob).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /start re-ocr/i }));
    await waitFor(() =>
      expect(mocked.createJob).toHaveBeenCalledWith({
        inbox: true,
        ocr_only: true,
        apply_policy: "review",
        instructions: undefined,
      }),
    );
  });

  it("vanishes when the inbox is clear", async () => {
    mocked.getInbox.mockResolvedValue({ count: 0, page_size: 25, all: null, results: [] });
    renderWithProviders(<Dashboard />);
    await screen.findByText(/every document has been analyzed/i);
    expect(screen.queryByText(/documents waiting/)).toBeNull();
  });
});
