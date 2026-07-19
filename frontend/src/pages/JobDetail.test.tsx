// The job page: pause/continue, bulk retry (all & multiselect), and
// the ONE list pattern for its sessions.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import JobDetail from "./JobDetail";
import { api, type Job, type Session, type SessionPage } from "../api";

vi.mock("../api", () => ({
  api: {
    getJob: vi.fn(),
    getJobAttention: vi.fn(),
    listSessions: vi.fn(),
    pauseJob: vi.fn(),
    resumeJob: vi.fn(),
    retryJob: vi.fn(),
    cancelJob: vi.fn(),
    archiveSession: vi.fn(),
    unarchiveSession: vi.fn(),
    cancelSession: vi.fn(),
  },
}));
const mocked = vi.mocked(api);

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 7,
    kind: "bulk_analyze",
    params: { label: "Inbox" },
    status: "running",
    total: 3,
    done: 1,
    failed: 1,
    stopped: 0,
    error: null,
    created_at: "2026-07-17T10:00:00Z",
    updated_at: "2026-07-17T10:01:00Z",
    ...overrides,
  };
}

function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    id: 40,
    agent_kind: "document",
    entity_type: "document",
    entity_id: 7,
    entity_name: "Rechnung RE1001",
    title: "Analysis",
    status: "idle",
    phase: "done",
    params: {},
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

function page(results: Session[]): SessionPage {
  return { count: results.length, page: 1, page_size: 25, results };
}

const renderPage = () =>
  renderWithProviders(<JobDetail />, { route: "/jobs/7", path: "/jobs/:id" });

describe("JobDetail — job control", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getJobAttention.mockResolvedValue({ next_session_id: null, remaining: 0 });
    mocked.listSessions.mockResolvedValue(
      page([
        makeSession({ id: 40, entity_name: "Rechnung RE1001" }),
        makeSession({ id: 41, entity_name: "Form 1040", status: "failed", phase: "analyzing" }),
      ]),
    );
  });

  it("shows document names (not just the run title) and a status filter", async () => {
    mocked.getJob.mockResolvedValue(makeJob());
    renderPage();
    expect(await screen.findByText("Rechnung RE1001")).toBeInTheDocument();
    expect(screen.getByText("Form 1040")).toBeInTheDocument();
    expect(screen.getByLabelText("filter by status")).toBeInTheDocument();
  });

  it("Pause pauses a running job; a paused job offers Continue", async () => {
    mocked.getJob.mockResolvedValue(makeJob());
    mocked.pauseJob.mockResolvedValue(makeJob({ status: "paused" }));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Pause/ }));
    expect(mocked.pauseJob).toHaveBeenCalledWith(7);

    vi.clearAllMocks();
    mocked.getJobAttention.mockResolvedValue({ next_session_id: null, remaining: 0 });
    mocked.listSessions.mockResolvedValue(page([makeSession()]));
    mocked.getJob.mockResolvedValue(makeJob({ status: "paused" }));
    mocked.resumeJob.mockResolvedValue(makeJob());
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Continue/ }));
    expect(mocked.resumeJob).toHaveBeenCalledWith(7);
  });

  it("Retry N failed retries everything; selecting rows retries the selection", async () => {
    mocked.getJob.mockResolvedValue(makeJob({ failed: 1, stopped: 1 }));
    mocked.retryJob.mockResolvedValue({ retried: 2 });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Retry 2 failed/ }));
    expect(mocked.retryJob).toHaveBeenCalledWith(7, undefined);

    // multiselect: pick one row, the toolbar retries just that session
    await userEvent.click(screen.getByLabelText("select session 41"));
    await userEvent.click(screen.getByRole("button", { name: /Retry 1 selected/ }));
    expect(mocked.retryJob).toHaveBeenCalledWith(7, [41]);
  });
});
