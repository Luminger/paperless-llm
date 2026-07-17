import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { ProposalCard } from "../components/ProposalCard";
import { renderWithProviders, makeProposal } from "../test/utils";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    patchProposal: vi.fn(),
    proposalAction: vi.fn(),
    getDocument: vi.fn(),
    listTags: vi.fn(),
    listCorrespondents: vi.fn(),
    listDocumentTypes: vi.fn(),
    listStoragePaths: vi.fn(),
  },
}));
const mocked = vi.mocked(api);

const DOC = {
  id: 7,
  title: "scan_0001",
  correspondent: 2,
  document_type: null,
  storage_path: null,
  tags: [6],
  created: "2024-04-17",
  added: "2026-07-17",
  archive_serial_number: null,
};

/** Agent proposes: new title, type Rechnung, +tag Rechnung, -tag scan. */
function metadataProposal(overrides = {}) {
  return makeProposal({
    agent_payload: {
      kind: "update_document_metadata",
      document_id: 7,
      reason: "Better title for the invoice",
      title: "Rechnung 4711-2024-0417",
      document_type: 1,
      add_tags: [1],
      remove_tags: [6],
    },
    ...overrides,
  });
}

function setupTaxonomy() {
  mocked.getDocument.mockResolvedValue(DOC);
  mocked.listTags.mockResolvedValue([
    { id: 1, name: "Rechnung" },
    { id: 6, name: "scan" },
    { id: 3, name: "wichtig" },
  ]);
  mocked.listCorrespondents.mockResolvedValue([
    { id: 2, name: "Telarko Deutschland GmbH" },
  ]);
  mocked.listDocumentTypes.mockResolvedValue([{ id: 1, name: "Rechnung" }]);
  mocked.listStoragePaths.mockResolvedValue([]);
}

function renderCard(p: ReturnType<typeof makeProposal>) {
  return renderWithProviders(<ProposalCard proposal={p} />);
}

describe("ProposalCard — metadata editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupTaxonomy();
  });

  it("resolves ids to names and shows unchanged fields too", async () => {
    renderCard(metadataProposal());

    // Correspondent is NOT part of the proposal but still visible, by name.
    expect((await screen.findAllByText("Telarko Deutschland GmbH")).length).toBeGreaterThan(0);
    // Current column shows the current tag by name; proposed shows the new set.
    expect(screen.getByText("Tags")).toBeInTheDocument();
    expect(await screen.findByText("Rechnung", { selector: "span" })).toBeInTheDocument();
    // No raw ids on display.
    expect(screen.queryByText("#1")).not.toBeInTheDocument();
    // document_id and reason are not editable rows.
    expect(screen.queryByText("document_id")).not.toBeInTheDocument();
    // Reason appears once, as context.
    expect(screen.getByText(/Agent's reasoning/)).toBeInTheDocument();
  });

  it("has a single tags field: removing a chip yields a remove_tags diff", async () => {
        mocked.patchProposal.mockResolvedValue(metadataProposal());
    renderCard(metadataProposal());

    // Desired set is [Rechnung] (6 removed, 1 added by the agent).
    const chip = await screen.findByRole("button", { name: /remove tag Rechnung/ });
    await userEvent.click(chip); // user drops the agent's added tag

    await userEvent.click(screen.getByRole("button", { name: "Save edits" }));
    await waitFor(() => expect(mocked.patchProposal).toHaveBeenCalled());
    const [, payload] = mocked.patchProposal.mock.calls[0];
    // Desired [] vs current [6] -> only remove_tags survives; add gone.
    expect(payload).toMatchObject({ document_id: 7, remove_tags: [6] });
    expect(payload).not.toHaveProperty("add_tags");
  });

  it("editing the title saves a diff against paperless, keeping context fields", async () => {
        mocked.patchProposal.mockResolvedValue(metadataProposal());
    renderCard(metadataProposal());

    const title = (await screen.findAllByRole("textbox"))[0] as HTMLInputElement;
    expect(title.value).toBe("Rechnung 4711-2024-0417");
    await userEvent.clear(title);
    await userEvent.type(title, "Meine Rechnung");

    await userEvent.click(screen.getByRole("button", { name: "Save edits" }));
    await waitFor(() => expect(mocked.patchProposal).toHaveBeenCalled());
    const [, payload] = mocked.patchProposal.mock.calls[0];
    expect(payload).toMatchObject({
      title: "Meine Rechnung",
      document_id: 7,
      reason: "Better title for the invoice",
    });
  });

  it("workflow: apply and reject only — no separate approve — plus hint text", async () => {
        mocked.proposalAction.mockResolvedValue(metadataProposal({ status: "applied" }));
    renderCard(metadataProposal());
    await screen.findAllByText("Telarko Deutschland GmbH");

    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Apply to paperless" }));
    await waitFor(() => expect(mocked.proposalAction).toHaveBeenCalledWith(1, "apply"));
  });

  it("rejected proposals are read-only", async () => {
    renderCard(metadataProposal({ status: "rejected" }));
    await screen.findAllByText("Telarko Deutschland GmbH");

    expect(screen.queryByRole("button", { name: "Apply to paperless" })).not.toBeInTheDocument();
    for (const input of screen.getAllByRole("textbox")) expect(input).toBeDisabled();
    expect(screen.queryByRole("button", { name: /remove tag/ })).not.toBeInTheDocument();
  });

  it("applied proposals offer revert", async () => {
        mocked.proposalAction.mockResolvedValue(
      metadataProposal({ status: "applied", applied: true, reverted: true }),
    );
    renderCard(metadataProposal({ status: "applied", applied: true, reverted: false }),);

    const revert = await screen.findByRole("button", { name: "Revert" });
    await userEvent.click(revert);
    await waitFor(() => expect(mocked.proposalAction).toHaveBeenCalledWith(1, "revert"));
  });
});

describe("ProposalCard — generic editor (other kinds)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupTaxonomy();
  });

  it("renders non-metadata kinds with agent/your-version columns, hiding reason", async () => {
    renderCard(makeProposal({
        kind: "merge_entities",
        agent_payload: {
          kind: "merge_entities",
          reason: "Duplicate correspondents",
          entity_type: "correspondent",
          source_id: 4,
          target_id: 2,
        },
      }),);

    expect(await screen.findByText("Agent proposed")).toBeInTheDocument();
    expect(screen.getByText("Your version")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).queryByText("reason")).not.toBeInTheDocument();
    expect(within(table).getByText("source_id")).toBeInTheDocument();
  });
});
