import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import Documents from "./Documents";
import { makeEntity, pickOption, renderWithProviders } from "../test/utils";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    listDocuments: vi.fn(),
    listTags: vi.fn(),
    listCorrespondents: vi.fn(),
    listDocumentTypes: vi.fn(),
    createJob: vi.fn(),
    getSyncStatus: vi.fn(),
  },
}));
const mocked = vi.mocked(api);

const DOCS = {
  count: 2,
  all: [7, 12, 99],
  results: [
    { id: 7, title: "scan_0001", correspondent: null, document_type: null,
      storage_path: null, tags: [], created: "2024-04-17", added: null,
      archive_serial_number: null },
    { id: 12, title: "Invoice 4-8", correspondent: 1, document_type: 2,
      storage_path: null, tags: [3], created: "1958-05-02", added: null,
      archive_serial_number: null },
  ],
};

describe("Documents", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getSyncStatus.mockResolvedValue({ resources: {} });
    mocked.listDocuments.mockResolvedValue(DOCS);
    mocked.listTags.mockResolvedValue([makeEntity({ id: 3, name: "Steuern" })]);
    mocked.listCorrespondents.mockResolvedValue([makeEntity({ id: 1, name: "Kraxi" })]);
    mocked.listDocumentTypes.mockResolvedValue([makeEntity({ id: 2, name: "Rechnung" })]);
  });

  it("rows link to the document page; no Analyze button", async () => {
    renderWithProviders(<Documents />);
    const link = await screen.findByRole("link", { name: "Invoice 4-8" });
    expect(link.getAttribute("href")).toBe("/documents/12");
    expect(screen.queryByRole("button", { name: "Analyze" })).not.toBeInTheDocument();
  });

  it("filters by tag/correspondent/type names", async () => {
    renderWithProviders(<Documents />);
    await screen.findByText("Invoice 4-8");

    await pickOption("filter by tag", "Steuern");
    await pickOption("filter by correspondent", "Kraxi");
    await waitFor(() =>
      expect(mocked.listDocuments).toHaveBeenLastCalledWith(
        expect.objectContaining({ tag_id: 3, correspondent_id: 1 }),
      ),
    );
  });

  it("multiselect: select-all spans all pages, bulk analyze creates a job", async () => {
    mocked.createJob.mockResolvedValue({ id: 4 } as never);
    renderWithProviders(<Documents />);
    await screen.findByText("Invoice 4-8");

    await userEvent.click(screen.getByRole("button", { name: "Select…" }));
    await userEvent.click(screen.getByLabelText("select document 7"));
    expect(screen.getByText("1 selected")).toBeInTheDocument();

    // Select all uses the cross-page id list from paperless (3 ids).
    await userEvent.click(screen.getByRole("button", { name: "Select all" }));
    expect(screen.getByText("3 selected")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /as job/ }));
    await waitFor(() =>
      expect(mocked.createJob).toHaveBeenCalledWith({ document_ids: [7, 12, 99] }),
    );
  });
});
