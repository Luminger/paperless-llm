import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import Jobs from "./Jobs";
import { renderWithProviders } from "../test/utils";
import { api, type Job } from "../api";

vi.mock("../api", () => ({
  api: {
    listJobs: vi.fn(),
    createJob: vi.fn(),
    cancelJob: vi.fn(),
  },
}));
const mocked = vi.mocked(api);

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 1,
    kind: "bulk_analyze",
    params: { inbox: true, apply_policy: "review" },
    status: "running",
    total: 3,
    done: 1,
    failed: 0,
    error: null,
    created_at: "2026-07-17T10:00:00Z",
    updated_at: "2026-07-17T10:01:00Z",
    ...overrides,
  };
}

describe("Jobs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listJobs.mockResolvedValue([makeJob()]);
  });

  it("lists campaigns with progress and cancels running ones", async () => {
    mocked.cancelJob.mockResolvedValue(makeJob({ status: "cancelled" }));
    renderWithProviders(<Jobs />);

    expect(await screen.findByText("Inbox")).toBeInTheDocument();
    expect(screen.getByText("1 ok / 3")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(mocked.cancelJob).toHaveBeenCalledWith(1));
  });

  it("creates an inbox campaign with auto-apply", async () => {
    mocked.createJob.mockResolvedValue(makeJob({ id: 2 }));
    renderWithProviders(<Jobs />);

    await userEvent.click(await screen.findByRole("button", { name: "New campaign" }));
    await userEvent.click(screen.getByLabelText(/auto-apply proposals/));
    await userEvent.click(screen.getByRole("button", { name: "Start campaign" }));

    await waitFor(() => expect(mocked.createJob).toHaveBeenCalled());
    expect(mocked.createJob.mock.calls[0][0]).toMatchObject({
      inbox: true,
      apply_policy: "auto",
      redo_ocr: false,
    });
  });

  it("query campaigns require a query", async () => {
    renderWithProviders(<Jobs />);
    await userEvent.click(await screen.findByRole("button", { name: "New campaign" }));
    await userEvent.click(screen.getByLabelText("Search query"));
    expect(screen.getByRole("button", { name: "Start campaign" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("campaign query"), "Rechnung");
    expect(screen.getByRole("button", { name: "Start campaign" })).toBeEnabled();
  });
});
