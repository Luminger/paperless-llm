import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { ProposalCard } from "../components/ProposalCard";
import { makeEntity, makeProposal, renderWithProviders } from "../test/utils";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    patchProposal: vi.fn(),
    revertCheck: vi.fn(),
    proposalAction: vi.fn(),
    getDocument: vi.fn(),
    listTags: vi.fn(),
    listCorrespondents: vi.fn(),
    listDocumentTypes: vi.fn(),
    listStoragePaths: vi.fn(),
  },
}));
const mocked = vi.mocked(api);

beforeEach(() => {
  mocked.revertCheck.mockResolvedValue({ revert_noop: false });
});

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
    makeEntity({ id: 1, name: "Rechnung" }),
    makeEntity({ id: 6, name: "scan" }),
    makeEntity({ id: 3, name: "wichtig" }),
  ]);
  mocked.listCorrespondents.mockResolvedValue([
    makeEntity({ id: 2, name: "Telarko Deutschland GmbH" }),
  ]);
  mocked.listDocumentTypes.mockResolvedValue([makeEntity({ id: 1, name: "Rechnung" })]);
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

  it("workflow: apply and reject, plus hint text", async () => {
        mocked.proposalAction.mockResolvedValue(metadataProposal({ status: "applied" }));
    renderCard(metadataProposal());
    await screen.findAllByText("Telarko Deutschland GmbH");


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

  it("shows merge context as resolved names; identity fields are never rows", async () => {
    renderCard(makeProposal({
        kind: "merge_entities",
        agent_payload: {
          kind: "merge_entities",
          reason: "Duplicate correspondents",
          entity_type: "correspondent",
          source_id: 4,
          target_id: 2,
        },
        base_snapshot: {
          source: { id: 4, name: "Kraxi GmbH", document_count: 1 },
          target: { id: 2, name: "Kraxi", document_count: 5 },
        },
      }),);

    expect(await screen.findByText("Kraxi GmbH")).toBeInTheDocument();
    expect(screen.getByText("Kraxi")).toBeInTheDocument();
    expect(screen.getByText(/the target survives/)).toBeInTheDocument();
    // No identity/reason rows, no editable fields at all for a merge.
    expect(screen.queryByText("source_id")).not.toBeInTheDocument();
    expect(screen.queryByText("entity_type")).not.toBeInTheDocument();
    expect(screen.queryByText("reason")).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("update_entity shows paperless-at-proposal-time vs editable proposed value", async () => {
    renderCard(makeProposal({
        kind: "update_entity",
        agent_payload: {
          kind: "update_entity",
          reason: "Fix casing",
          entity_type: "correspondent",
          entity_id: 7,
          name: "Internal Revenue Service",
        },
        base_snapshot: { name: "internal revenue service" },
      }),);

    expect(await screen.findByText("In paperless (at proposal time)")).toBeInTheDocument();
    expect(screen.getByText("Proposed")).toBeInTheDocument();
    // Left column: the snapshot value; right: editable input with proposal.
    expect(screen.getByText("internal revenue service")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Internal Revenue Service")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).queryByText("entity_id")).not.toBeInTheDocument();
  });
});

describe("ProposalCard — revert noop", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupTaxonomy();
  });

  it("greys out Revert with a tooltip when reverting would change nothing", async () => {
    mocked.revertCheck.mockResolvedValue({ revert_noop: true });
    renderCard(makeProposal({ status: "applied", applied: true }));

    const btn = await screen.findByRole("button", { name: "Revert" });
    await waitFor(() => expect(btn).toBeDisabled());
    expect(btn.getAttribute("title")).toMatch(/nothing to undo/);
  });

  it("keeps Revert active when the revert is real", async () => {
    mocked.revertCheck.mockResolvedValue({ revert_noop: false });
    renderCard(makeProposal({ status: "applied", applied: true }));
    const btn = await screen.findByRole("button", { name: "Revert" });
    await waitFor(() => expect(mocked.revertCheck).toHaveBeenCalled());
    expect(btn).toBeEnabled();
    expect(btn.getAttribute("title")).toMatch(/Restore the pre-apply state/);
  });
});
