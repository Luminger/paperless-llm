import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import Taxonomy from "./Taxonomy";
import { makeEntity, renderWithProviders } from "../test/utils";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    listTags: vi.fn(),
    listCorrespondents: vi.fn(),
    listDocumentTypes: vi.fn(),
    mergeCandidates: vi.fn(),
    analyzeEntity: vi.fn(),
    getSyncStatus: vi.fn(),
  },
}));
const mocked = vi.mocked(api);

describe("Taxonomy", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getSyncStatus.mockResolvedValue({ resources: {} });
    mocked.listTags.mockResolvedValue([
      makeEntity({ id: 1, name: "Steuern", document_count: 4,
        instructions: "Nur Steuerpost." }),
      makeEntity({ id: 2, name: "Inbox", document_count: 2, is_inbox_tag: true,
        instructions: "This is the inbox tag…" }),
      makeEntity({ id: 3, name: "Versicherung", document_count: 1 }),
    ]);
    mocked.mergeCandidates.mockResolvedValue([]);
  });

  it("rows link to detail pages, show instructions, no Analyze button", async () => {
    renderWithProviders(<Taxonomy />);

    const link = await screen.findByRole("link", { name: "Steuern" });
    expect(link.getAttribute("href")).toBe("/taxonomy/tag/1");
    expect(screen.getByText("Nur Steuerpost.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Analyze" })).not.toBeInTheDocument();
  });

  it("filters by name", async () => {
    renderWithProviders(<Taxonomy />);
    await screen.findByText("Steuern");
    await userEvent.type(screen.getByLabelText("filter entities"), "vers");
    expect(screen.queryByText("Steuern")).not.toBeInTheDocument();
    expect(screen.getByText("Versicherung")).toBeInTheDocument();
  });

  it("multiselect: select all skips inbox, bulk analyze fires per entity", async () => {
    mocked.analyzeEntity.mockResolvedValue({ id: 9 } as never);
    renderWithProviders(<Taxonomy />);
    await screen.findByText("Steuern");

    await userEvent.click(screen.getByRole("button", { name: "Select…" }));
    // Inbox checkbox disabled.
    expect(screen.getByLabelText("select Inbox")).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Select all" }));
    expect(screen.getByText("2 selected")).toBeInTheDocument(); // inbox skipped

    await userEvent.click(screen.getByRole("button", { name: /Analyze 2 tag/ }));
    await waitFor(() => expect(mocked.analyzeEntity).toHaveBeenCalledTimes(2));
    expect(mocked.analyzeEntity).toHaveBeenCalledWith("tag", 1);
    expect(mocked.analyzeEntity).toHaveBeenCalledWith("tag", 3);
  });

  it("multiselect can be cancelled", async () => {
    renderWithProviders(<Taxonomy />);
    await screen.findByText("Steuern");
    await userEvent.click(screen.getByRole("button", { name: "Select…" }));
    await userEvent.click(screen.getByLabelText("select Steuern"));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("select Steuern")).not.toBeInTheDocument();
  });
});
