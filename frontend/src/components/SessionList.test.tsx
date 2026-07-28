// The session table's attention column tells the user what (if
// anything) a run wants from them — cover every badge branch, the
// stop/archive affordances and the list's empty states.

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { SessionList, SessionTable } from "./SessionList";
import { renderWithProviders } from "../test/utils";
import { api, type Session, type SessionPage } from "../api";

vi.mock("../api", () => ({
  api: {
    listSessions: vi.fn(),
    archiveSession: vi.fn(),
    unarchiveSession: vi.fn(),
    cancelSession: vi.fn(),
  },
}));
const mocked = vi.mocked(api);

function mkSession(over: Partial<Session> = {}): Session {
  return {
    id: 4,
    agent_kind: "document",
    entity_type: "document",
    entity_id: 7,
    entity_name: "",
    title: "Document #7 analysis",
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
    ...over,
  };
}

function page(results: Session[], count = results.length): SessionPage {
  return { count, page: 1, page_size: 5, results };
}

const renderTable = (sessions: Session[], showEntity = true) =>
  renderWithProviders(<SessionTable sessions={sessions} showEntity={showEntity} />);

describe("SessionTable — attention badges", () => {
  beforeEach(() => vi.clearAllMocks());

  it("pending proposals count, singular and plural", () => {
    renderTable([
      mkSession({ id: 1, pending_proposal_count: 1, proposal_count: 1 }),
      mkSession({ id: 2, pending_proposal_count: 3, proposal_count: 3 }),
    ]);
    expect(screen.getByText("proposal to review")).toBeInTheDocument();
    expect(screen.getByText("3 proposals to review")).toBeInTheDocument();
  });

  it("says what HAPPENED once proposals are applied", () => {
    renderTable([
      mkSession({ proposal_count: 2, applied_proposal_count: 2 }),
    ]);
    expect(screen.getByText("2 applied")).toBeInTheDocument();
  });

  it("declined-everything runs read as nothing applied", () => {
    renderTable([mkSession({ proposal_count: 2 })]);
    expect(screen.getByText("nothing applied")).toBeInTheDocument();
  });

  it("finished without proposals is explicit; unfinished stays silent", () => {
    renderTable([
      mkSession({ id: 1, phase: "done" }),
      mkSession({ id: 2, phase: "analyzing", status: "running" }),
    ]);
    expect(screen.getAllByText("no changes proposed")).toHaveLength(1);
  });

  it("the OCR gate badge shows for live runs but not archived ones", () => {
    renderTable([
      mkSession({ id: 1, phase: "ocr_review" }),
      mkSession({ id: 2, phase: "ocr_review", archived_at: "2026-07-17T12:00:00Z" }),
    ]);
    expect(screen.getAllByText("OCR review needed")).toHaveLength(1);
  });

  it("shows the entity name first with the run title as a side note", () => {
    renderTable([mkSession({ entity_name: "Kraxi Rechnung" })]);
    const link = screen.getByRole("link", { name: "Kraxi Rechnung" });
    expect(link.getAttribute("href")).toBe("/sessions/4");
    expect(screen.getByText("Document #7 analysis")).toBeInTheDocument();
  });
});

describe("SessionTable — row actions", () => {
  beforeEach(() => vi.clearAllMocks());

  it("only running or queued rows can be stopped", async () => {
    mocked.cancelSession.mockResolvedValue(mkSession());
    renderTable([
      mkSession({ id: 1, status: "running", phase: "analyzing" }),
      mkSession({ id: 2, status: "idle", phase: "queued" }),
      mkSession({ id: 3, status: "idle", phase: "done" }),
    ]);
    const stops = screen.getAllByRole("button", { name: /Stop/ });
    expect(stops).toHaveLength(2);
    await userEvent.click(stops[0]);
    await waitFor(() => expect(mocked.cancelSession).toHaveBeenCalledWith(1));
  });

  it("archived rows offer Unarchive and are never stoppable", async () => {
    mocked.unarchiveSession.mockResolvedValue(mkSession());
    renderTable([
      mkSession({ status: "running", archived_at: "2026-07-17T12:00:00Z" }),
    ]);
    expect(screen.queryByRole("button", { name: /Stop/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Unarchive" }));
    await waitFor(() => expect(mocked.unarchiveSession).toHaveBeenCalledWith(4));
    expect(mocked.archiveSession).not.toHaveBeenCalled();
  });
});

describe("SessionList — states", () => {
  beforeEach(() => vi.clearAllMocks());

  it("empty attention list has its calm empty state", async () => {
    mocked.listSessions.mockResolvedValue(page([]));
    renderWithProviders(<SessionList unfinished />);
    expect(await screen.findByText("Nothing needs attention.")).toBeInTheDocument();
  });

  it("plain lists say no sessions yet; archived stays collapsed", async () => {
    mocked.listSessions.mockResolvedValue(page([]));
    renderWithProviders(<SessionList />);
    expect(await screen.findByText("No sessions yet.")).toBeInTheDocument();
    expect(screen.getByText("Archived sessions")).toBeInTheDocument();
    // Collapsed: the archived query has not been made yet.
    expect(mocked.listSessions).toHaveBeenCalledTimes(1);
    expect(mocked.listSessions).toHaveBeenCalledWith(
      expect.objectContaining({ archived: false }),
    );
  });

  it("a failing query surfaces the error notice", async () => {
    mocked.listSessions.mockRejectedValue(new Error("backend down"));
    renderWithProviders(<SessionList showArchived={false} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("backend down");
  });

  it("scopes the query to the entity it renders under", async () => {
    mocked.listSessions.mockResolvedValue(page([mkSession()]));
    renderWithProviders(
      <SessionList entityType="tag" entityId={5} showArchived={false} />,
    );
    await screen.findByText("no changes proposed");
    expect(mocked.listSessions).toHaveBeenCalledWith(
      expect.objectContaining({ entity_type: "tag", entity_id: 5 }),
    );
  });
});
