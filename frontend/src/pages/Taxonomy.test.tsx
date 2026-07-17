import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import Taxonomy from "./Taxonomy";
import { renderWithProviders } from "../test/utils";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    listTags: vi.fn(),
    listCorrespondents: vi.fn(),
    listDocumentTypes: vi.fn(),
    mergeCandidates: vi.fn(),
    analyzeEntity: vi.fn(),
  },
}));
const mocked = vi.mocked(api);

describe("Taxonomy", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listTags.mockResolvedValue([
      { id: 1, name: "Steuern", document_count: 4, match: "", is_inbox_tag: false },
      { id: 2, name: "Inbox", document_count: 2, match: "", is_inbox_tag: true },
    ]);
    mocked.listCorrespondents.mockResolvedValue([
      { id: 4, name: "Kraxi GmbH", document_count: 1 },
      { id: 8, name: "Kraxi", document_count: 5 },
    ]);
    mocked.mergeCandidates.mockResolvedValue([]);
  });

  it("lists entities with counts and inbox badge; analyze starts a session", async () => {
    mocked.analyzeEntity.mockResolvedValue({ id: 42 } as never);
    renderWithProviders(<Taxonomy />);

    expect(await screen.findByText("Steuern")).toBeInTheDocument();
    expect(screen.getByText("inbox")).toBeInTheDocument();

    await userEvent.click(screen.getAllByRole("button", { name: "Analyze" })[0]);
    await waitFor(() =>
      expect(mocked.analyzeEntity).toHaveBeenCalledWith("tag", 1, undefined),
    );
  });

  it("shows merge candidates and reviews them with target context", async () => {
    mocked.mergeCandidates.mockResolvedValue([
      {
        entity_type: "correspondent",
        source: { id: 4, name: "Kraxi GmbH", document_count: 1 },
        target: { id: 8, name: "Kraxi", document_count: 5 },
        string_score: 0.9,
        semantic_score: null,
      },
    ]);
    mocked.analyzeEntity.mockResolvedValue({ id: 43 } as never);
    renderWithProviders(<Taxonomy />);

    await userEvent.click(await screen.findByRole("button", { name: "Correspondents" }));
    expect(await screen.findByText(/Possible duplicates \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/90% similar/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Review with agent" }));
    await waitFor(() => expect(mocked.analyzeEntity).toHaveBeenCalled());
    const [type, id, instructions] = mocked.analyzeEntity.mock.calls[0];
    expect(type).toBe("correspondent");
    expect(id).toBe(4); // the source (worse) entity is reviewed
    expect(instructions).toContain('"Kraxi" (id=8)');
  });
});
