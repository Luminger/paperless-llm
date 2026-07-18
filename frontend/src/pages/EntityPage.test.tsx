import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import EntityPage from "./EntityPage";
import { makeEntity, renderWithProviders } from "../test/utils";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    getMeta: vi.fn(),
    getDocument: vi.fn(),
    getEntity: vi.fn(),
    listSessions: vi.fn(),
    listTags: vi.fn(),
    listCorrespondents: vi.fn(),
    listDocumentTypes: vi.fn(),
    listStoragePaths: vi.fn(),
    analyzeDocument: vi.fn(),
    setInstructions: vi.fn(),
    analyzeEntity: vi.fn(),
    archiveSession: vi.fn(),
    unarchiveSession: vi.fn(),
  },
}));
const mocked = vi.mocked(api);

describe("EntityPage (generic entity overview)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getMeta.mockResolvedValue({ paperless_url: "http://paperless.example", version: "test" });
    mocked.listSessions.mockResolvedValue({ count: 0, page: 1, page_size: 5, results: [] });
    mocked.listTags.mockResolvedValue([makeEntity({ id: 3, name: "Steuern" })]);
    mocked.listCorrespondents.mockResolvedValue([makeEntity({ id: 8, name: "Kraxi" })]);
    mocked.listDocumentTypes.mockResolvedValue([makeEntity({ id: 2, name: "Rechnung" })]);
    mocked.listStoragePaths.mockResolvedValue([]);
  });

  it("documents: facts with entity links, paperless deep link, sessions list", async () => {
    mocked.getDocument.mockResolvedValue({
      id: 12,
      title: "Invoice 4-8",
      correspondent: 8,
      document_type: 2,
      storage_path: null,
      tags: [3],
      created: "1958-05-02",
      added: "2026-07-16",
      archive_serial_number: null,
    });
    renderWithProviders(<EntityPage />, { route: "/documents/12", path: "/documents/:id" });

    expect(await screen.findByRole("heading", { name: "Invoice 4-8" })).toBeInTheDocument();
    // Entity-valued fields link to their own detail pages.
    expect((await screen.findByRole("link", { name: "Kraxi" })).getAttribute("href")).toBe(
      "/taxonomy/correspondent/8",
    );
    expect((await screen.findByRole("link", { name: "Rechnung" })).getAttribute("href")).toBe(
      "/taxonomy/document_type/2",
    );
    expect((await screen.findByRole("link", { name: "Steuern" })).getAttribute("href")).toBe(
      "/taxonomy/tag/3",
    );
    // Back to the entry in paperless.
    expect(
      screen.getByRole("link", { name: /open in paperless/ }).getAttribute("href"),
    ).toBe("http://paperless.example/documents/12/details");
    // Preview + session list present.
    expect(screen.getByAltText("document preview")).toBeInTheDocument();
    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(await screen.findByText("No sessions yet.")).toBeInTheDocument();
  });

  it("taxonomy entities use the same generic page", async () => {
    mocked.getEntity.mockResolvedValue(makeEntity({
      id: 8,
      name: "Kraxi",
      document_count: 5,
      match: "kraxi",
      matching_algorithm: 1,
    }));
    renderWithProviders(<EntityPage />, {
      route: "/taxonomy/correspondent/8",
      path: "/taxonomy/:type/:id",
    });

    expect(await screen.findByRole("heading", { name: "Kraxi" })).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText(/any word · “kraxi”/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /open in paperless/ }).getAttribute("href"),
    ).toBe("http://paperless.example/correspondents");
    expect(screen.getByRole("button", { name: "Analyze" })).toBeInTheDocument();
    // Taxonomy entities get the instructions editor.
    expect(screen.getByLabelText("agent instructions")).toBeInTheDocument();
  });
});

describe("EntityPage — instructions & inbox", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getMeta.mockResolvedValue({ paperless_url: "http://paperless.example", version: "test" });
    mocked.listSessions.mockResolvedValue({ count: 0, page: 1, page_size: 5, results: [] });
  });

  it("saves agent instructions", async () => {
    mocked.getEntity.mockResolvedValue(makeEntity({
      id: 3, name: "Steuern", document_count: 4, instructions: "old rule",
    }));
    mocked.setInstructions.mockResolvedValue({ entity_type: "tag", entity_id: 3, instructions: "Nur Steuerpost." });
    renderWithProviders(<EntityPage />, {
      route: "/taxonomy/tag/3",
      path: "/taxonomy/:type/:id",
    });

    const ta = await screen.findByLabelText("agent instructions");
    expect(ta).toHaveValue("old rule");
    await userEvent.clear(ta);
    await userEvent.type(ta, "Nur Steuerpost.");
    await userEvent.click(screen.getByRole("button", { name: "Save instructions" }));
    await waitFor(() =>
      expect(mocked.setInstructions).toHaveBeenCalledWith("tag", 3, "Nur Steuerpost."),
    );
  });

  it("inbox tag is not analyzable", async () => {
    mocked.getEntity.mockResolvedValue(makeEntity({
      id: 1, name: "Inbox", document_count: 2, is_inbox_tag: true,
      instructions: "This is the inbox tag…",
    }));
    renderWithProviders(<EntityPage />, {
      route: "/taxonomy/tag/1",
      path: "/taxonomy/:type/:id",
    });
    expect(await screen.findByText(/not analyzable \(inbox\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Analyze" })).not.toBeInTheDocument();
  });
});
