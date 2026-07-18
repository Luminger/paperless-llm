import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import AuditLog from "./AuditLog";
import { renderWithProviders } from "../test/utils";
import { api, type AuditEntry } from "../api";

vi.mock("../api", () => ({
  api: { listAudit: vi.fn() },
}));
const mocked = vi.mocked(api);

function entry(overrides: Partial<AuditEntry>): AuditEntry {
  return {
    id: 1,
    ts: "2026-07-17T12:00:00Z",
    kind: "proposal",
    action: "applied",
    actor: "user",
    detail: {},
    ...overrides,
  };
}

describe("AuditLog", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows collapsed rows with actor; expanding reveals the diff", async () => {
    mocked.listAudit.mockResolvedValue({
      count: 1,
      page: 1,
      page_size: 20,
      results: [
        entry({
          detail: {
            proposal_id: 5,
            proposal_kind: "update_document_metadata",
            session_id: 2,
            diff: { title: { from: "scan_0001", to: "Invoice 4-8" } },
          },
        }),
      ],
    });
    renderWithProviders(<AuditLog />);

    expect(await screen.findByText(/proposal #5/)).toBeInTheDocument();
    expect(screen.getByText("user")).toBeInTheDocument();
    // Diff hidden until expanded.
    expect(screen.queryByText("scan_0001")).not.toBeInTheDocument();
    await userEvent.click(screen.getByText(/proposal #5/));
    expect(screen.getByText('"scan_0001"')).toBeInTheDocument();
    expect(screen.getByText('"Invoice 4-8"')).toBeInTheDocument();
  });

  it("renders paperless traffic and filters by kind", async () => {
    mocked.listAudit.mockResolvedValue({
      count: 1,
      page: 1,
      page_size: 20,
      results: [
        entry({
          kind: "paperless",
          action: "fetch",
          actor: "system",
          detail: { method: "GET", path: "/api/documents/", resource: "documents", status: 200 },
        }),
      ],
    });
    renderWithProviders(<AuditLog />);

    expect(await screen.findByText(/GET \/api\/documents\//)).toBeInTheDocument();
    expect(screen.getByText("system")).toBeInTheDocument();

    // The kind filter is a dropdown like every other list filter.
    await userEvent.click(screen.getByRole("combobox", { name: "filter by log kind" }));
    await userEvent.click(screen.getByRole("option", { name: "data changes" }));
    expect(mocked.listAudit).toHaveBeenLastCalledWith(1, 20, "changes");
  });
});

describe("AuditLog — scheduled tasks", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders task entries and offers the Tasks filter", async () => {
    mocked.listAudit.mockResolvedValue({
      count: 2,
      page: 1,
      page_size: 20,
      results: [
        entry({
          id: 10,
          kind: "task",
          action: "scheduled",
          actor: "user",
          detail: { step_kind: "analysis", session_id: 4, lane: "interactive" },
        }),
        entry({
          id: 11,
          kind: "task",
          action: "retry_scheduled",
          actor: "system",
          detail: { step_kind: "ocr", session_id: 4, attempt: 1 },
        }),
      ],
    });
    renderWithProviders(<AuditLog />);

    expect(await screen.findByText(/queued.*analysis step/)).toBeInTheDocument();
    expect(
      screen.getByText(/automatic retry 1 of ocr step scheduled/),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("combobox", { name: "filter by log kind" }));
    await userEvent.click(screen.getByRole("option", { name: "tasks" }));
    expect(mocked.listAudit).toHaveBeenLastCalledWith(1, 20, "task");
  });
});
