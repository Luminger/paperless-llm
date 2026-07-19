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
    getDocumentHistory: vi.fn(),
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
    mocked.getDocumentHistory.mockResolvedValue([]);
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
      screen.getByRole("link", { name: /Open in paperless/ }).getAttribute("href"),
    ).toBe("http://paperless.example/documents/12/details");
    // Preview (clickable), distinct actions, content, session list, history.
    expect(screen.getByRole("button", { name: /open preview of Invoice 4-8/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /re-do ocr/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start analysis/i })).toBeInTheDocument();
    expect(screen.getByText(/no text layer/i)).toBeInTheDocument();
    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(await screen.findByText("No sessions yet.")).toBeInTheDocument();
    expect(screen.getByText("Change history")).toBeInTheDocument();
    expect(
      await screen.findByText(/no changes applied to this document yet/i),
    ).toBeInTheDocument();
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
    // Facts speak the proposal editor's vocabulary now.
    expect(screen.getByText("Auto-assignment")).toBeInTheDocument();
    expect(screen.getByText(/Any word — needs a pattern/)).toBeInTheDocument();
    expect(screen.getByText("kraxi")).toBeInTheDocument();
    expect(screen.getByText("Ignore case")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Open in paperless/ }).getAttribute("href"),
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

describe("EntityPage document history", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getMeta.mockResolvedValue({ paperless_url: "http://paperless.example", version: "t" });
    mocked.listSessions.mockResolvedValue({ count: 0, page: 1, page_size: 5, results: [] });
    mocked.listTags.mockResolvedValue([]);
    mocked.listCorrespondents.mockResolvedValue([]);
    mocked.listDocumentTypes.mockResolvedValue([]);
    mocked.listStoragePaths.mockResolvedValue([]);
    mocked.getDocument.mockResolvedValue({
      id: 12, title: "Invoice 4-8", correspondent: null, document_type: null,
      storage_path: null, tags: [], created: "1958-05-02", added: "2026-07-16",
      archive_serial_number: null, content: "some text",
    });
  });

  it("attributes changes and links their sessions", async () => {
    mocked.getDocumentHistory.mockResolvedValue([
      {
        proposal_id: 4, session_id: 9, session_title: "First pass",
        kind: "update_document_metadata", fields: ["correspondent", "title"],
        applied_at: "2026-07-02T10:00:00Z", applied_by: "user:simon",
        edited: true, reverted: false,
      },
      {
        proposal_id: 3, session_id: 9, session_title: "First pass",
        kind: "replace_content", fields: ["content"],
        applied_at: "2026-07-01T10:00:00Z", applied_by: "system",
        edited: false, reverted: true,
      },
    ]);
    renderWithProviders(<EntityPage />, { route: "/documents/12", path: "/documents/:id" });
    expect(await screen.findByText("Metadata updated")).toBeInTheDocument();
    expect(screen.getByText("(correspondent, title)")).toBeInTheDocument();
    expect(screen.getByText("simon")).toBeInTheDocument();
    expect(screen.getByText("automatic")).toBeInTheDocument();
    expect(screen.getByText("Content replaced (OCR)")).toBeInTheDocument();
    expect(screen.getByText("reverted")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: "First pass" });
    expect(links[0].getAttribute("href")).toBe("/sessions/9");
  });
});
