import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import Jobs from "./Jobs";
import { jobPage, makeEntity, renderWithProviders } from "../test/utils";
import { api, type Job } from "../api";

vi.mock("../api", () => ({
  api: {
    listJobs: vi.fn(),
    createJob: vi.fn(),
    cancelJob: vi.fn(),
    listTags: vi.fn(),
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
    mocked.listJobs.mockResolvedValue(jobPage([makeJob()]));
    mocked.listTags.mockResolvedValue([
      makeEntity({ id: 1, name: "Inbox", is_inbox_tag: true }),
      makeEntity({ id: 3, name: "Steuern" }),
    ]);
  });

  it("lists jobs with progress and cancels running ones", async () => {
    mocked.cancelJob.mockResolvedValue(makeJob({ status: "cancelled" }));
    renderWithProviders(<Jobs />);

    expect(await screen.findByText("Inbox")).toBeInTheDocument();
    expect(screen.getByText("1 ok / 3")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(mocked.cancelJob).toHaveBeenCalledWith(1));
  });

  it("creates an inbox job with auto-apply", async () => {
    mocked.createJob.mockResolvedValue(makeJob({ id: 2 }));
    renderWithProviders(<Jobs />);

    await userEvent.click(await screen.findByRole("button", { name: "New job" }));
    await userEvent.click(screen.getByLabelText(/auto-apply proposals/));
    await userEvent.click(screen.getByRole("button", { name: "Start job" }));

    await waitFor(() => expect(mocked.createJob).toHaveBeenCalled());
    expect(mocked.createJob.mock.calls[0][0]).toMatchObject({
      inbox: true,
      apply_policy: "auto",
      redo_ocr: false,
    });
  });

  it("tag jobs require a tag; inbox tag is not offered; no query scope", async () => {
    mocked.createJob.mockResolvedValue(makeJob({ id: 3 }));
    renderWithProviders(<Jobs />);
    await userEvent.click(await screen.findByRole("button", { name: "New job" }));

    // Free-text query scope is gone.
    expect(screen.queryByLabelText("Search query")).not.toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Documents with tag"));
    expect(screen.getByRole("button", { name: "Start job" })).toBeDisabled();

    const select = screen.getByLabelText("job tag");
    // The inbox tag cannot scope a job (it has its own scope).
    expect(
      screen.queryByRole("option", { name: "Inbox" }),
    ).not.toBeInTheDocument();
    await userEvent.selectOptions(select, "3");
    expect(screen.getByRole("button", { name: "Start job" })).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "Start job" }));
    await waitFor(() =>
      expect(mocked.createJob.mock.calls[0][0]).toMatchObject({ tag_id: 3 }),
    );
  });
});
