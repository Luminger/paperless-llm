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
    sendMessage: vi.fn(),
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
    expect(
      (await screen.findAllByText("Rechnung")).length,
    ).toBeGreaterThan(0);
    // No raw ids on display.
    expect(screen.queryByText("#1")).not.toBeInTheDocument();
    // document_id is not an editable row.
    expect(screen.queryByText("document_id")).not.toBeInTheDocument();
    // Reason appears once, as context.
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
    // No identity rows, no editable fields at all for a merge.
    expect(screen.queryByText("source_id")).not.toBeInTheDocument();
    expect(screen.queryByText("entity_type")).not.toBeInTheDocument();
    expect(screen.queryByText("reason")).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("unknown kinds fall back to the generic field editor (safety net)", async () => {
    renderCard(makeProposal({
        kind: "set_owner",
        agent_payload: {
          kind: "set_owner",
          entity_type: "correspondent",
          entity_id: 7,
          owner: "admin",
        },
        base_snapshot: { owner: "nobody" },
      }),);

    expect(await screen.findByText("In paperless (at proposal time)")).toBeInTheDocument();
    // Left column: the snapshot value; right: editable input.
    expect(screen.getByText("nobody")).toBeInTheDocument();
    expect(screen.getByDisplayValue("admin")).toBeInTheDocument();
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
    // The explanation moved from native title to the Tip wrapper (UI-U4):
    // hover the interposed span and read the Radix tooltip.
    await userEvent.hover(btn.parentElement!);
    expect(
      (await screen.findAllByText(/nothing to undo/)).length,
    ).toBeGreaterThan(0);
  });

  it("keeps Revert active when the revert is real", async () => {
    mocked.revertCheck.mockResolvedValue({ revert_noop: false });
    renderCard(makeProposal({ status: "applied", applied: true }));
    const btn = await screen.findByRole("button", { name: "Revert" });
    await waitFor(() => expect(mocked.revertCheck).toHaveBeenCalled());
    expect(btn).toBeEnabled();
    await userEvent.hover(btn.parentElement!);
    expect(
      (await screen.findAllByText(/Restore the pre-apply state/)).length,
    ).toBeGreaterThan(0);
  });
});

describe("ProposalCard — contextual steering", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupTaxonomy();
  });

  it("Ask the agent to revise sends a proposal-scoped message", async () => {
    mocked.sendMessage.mockResolvedValue({ id: 1 } as never);
    renderWithProviders(<ProposalCard proposal={makeProposal({ id: 5, session_id: 9 })} />);

    await userEvent.click(
      await screen.findByRole("button", { name: /Ask the agent to revise/ }),
    );
    await userEvent.type(
      screen.getByLabelText("revise proposal 5"),
      "use the German title",
    );
    await userEvent.click(screen.getByRole("button", { name: "Send to agent" }));
    await waitFor(() =>
      expect(mocked.sendMessage).toHaveBeenCalledWith(
        9,
        expect.stringMatching(/^About the update document metadata proposal: use the German title$/),
      ),
    );
  });
});

describe("ProposalCard — date field", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupTaxonomy();
    mocked.revertCheck.mockResolvedValue({ revert_noop: false });
  });

  it("shows the created date in the user's format and picks via calendar", async () => {
    localStorage.setItem("pllm.pref.dateFormat", "eu");
    mocked.patchProposal.mockResolvedValue(metadataProposal());
    renderCard(metadataProposal());

    // Trigger renders the formatted date, not a native input.
    const trigger = await screen.findByRole("button", { name: "created date" });
    expect(trigger).toHaveTextContent("17.04.2024");

    await userEvent.click(trigger);
    // The framework calendar opens; pick another day of that month.
    await screen.findByRole("grid");
    const day = screen
      .getAllByRole("button")
      .find((b) => b.textContent?.trim() === "20");
    expect(day).toBeTruthy();
    await userEvent.click(day!);
    await userEvent.click(screen.getByRole("button", { name: "Save edits" }));
    await waitFor(() => expect(mocked.patchProposal).toHaveBeenCalled());
    const [, payload] = mocked.patchProposal.mock.calls[0];
    expect(payload).toMatchObject({ created: "2024-04-20" });
    localStorage.clear();
  });
});

describe("ProposalCard — creations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupTaxonomy();
    mocked.revertCheck.mockResolvedValue({ revert_noop: false });
  });

  it("names the entity type and hides the paperless column for new entities", async () => {
    renderWithProviders(
      <ProposalCard
        proposal={makeProposal({
          kind: "create_entity",
          entity_type: "document_type",
          entity_id: null,
          agent_payload: {
            kind: "create_entity",
            entity_type: "document_type",
            name: "Tax Return",
          },
        })}
      />,
    );
    expect(await screen.findByText("create document type")).toBeInTheDocument();
    // A new entity has no current paperless state — no such column.
    expect(screen.queryByText(/In paperless \(at proposal time\)/)).not.toBeInTheDocument();
    expect(screen.getByText("Proposed")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Tax Return")).toBeInTheDocument();
  });
});

describe("ProposalCard — entity editors (named fields, no raw data)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupTaxonomy();
    mocked.revertCheck.mockResolvedValue({ revert_noop: false });
  });

  function updateTagProposal() {
    return makeProposal({
      kind: "update_entity",
      entity_type: "tag",
      entity_id: 6,
      agent_payload: {
        kind: "update_entity",
        entity_type: "tag",
        entity_id: 6,
        match: "scan scanned",
        matching_algorithm: 1,
      },
      base_snapshot: { match: "", matching_algorithm: 0 },
    });
  }

  it("update_entity: live current values, algorithm NAMES, no raw ids", async () => {
    renderCard(updateTagProposal());

    // named rows, not payload keys
    expect(await screen.findByText("Name")).toBeInTheDocument();
    expect(screen.getByText("Auto-assignment")).toBeInTheDocument();
    expect(screen.getByText("Match pattern")).toBeInTheDocument();
    // live current value from the taxonomy list (tag 6 = "scan")
    expect(screen.getByLabelText("entity name")).toHaveValue("scan");
    // the algorithm shows as a NAME in the select, not the number 1
    expect(screen.getByLabelText("matching mode")).toHaveTextContent(/any word/i);
    // current column: algorithm 0 shows its name
    expect(screen.getByText("none")).toBeInTheDocument();
    // nothing renders the raw payload key or numeric id
    expect(screen.queryByText("matching_algorithm")).not.toBeInTheDocument();
    expect(screen.queryByText("entity_id")).not.toBeInTheDocument();
  });

  it("update_entity: editing the name saves ONLY the changed field + identity", async () => {
    mocked.patchProposal.mockResolvedValue(updateTagProposal());
    renderCard(updateTagProposal());

    const nameInput = await screen.findByLabelText("entity name");
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "scanned");
    await userEvent.click(screen.getByRole("button", { name: "Save edits" }));

    await waitFor(() => expect(mocked.patchProposal).toHaveBeenCalled());
    const payload = mocked.patchProposal.mock.calls[0][1] as Record<string, unknown>;
    expect(payload).toEqual({
      entity_type: "tag",
      entity_id: 6,
      name: "scanned",
      match: "scan scanned",
      matching_algorithm: 1,
    });
  });

  it("create_entity: two-column layout with document chips instead of id arrays", async () => {
    mocked.getDocument.mockResolvedValue({ ...DOC, id: 7, title: "Telarko Rechnung" });
    renderCard(
      makeProposal({
        kind: "create_entity",
        entity_type: "correspondent",
        entity_id: null,
        agent_payload: {
          kind: "create_entity",
          entity_type: "correspondent",
          name: "Telarko GmbH",
          assign_to_documents: [7],
        },
      }),
    );

    expect(await screen.findByLabelText("entity name")).toHaveValue("Telarko GmbH");
    // the assigned document appears as a TITLE chip, not "[7]"
    expect(await screen.findByText("Telarko Rechnung")).toBeInTheDocument();
    expect(screen.queryByText("[7]")).not.toBeInTheDocument();
    expect(screen.queryByText("assign_to_documents")).not.toBeInTheDocument();
    // no "currently in paperless" column for a creation
    expect(screen.queryByText(/currently in paperless/i)).not.toBeInTheDocument();
  });

  it("merge_entities: prose only — names and doc counts from the snapshot", async () => {
    renderCard(
      makeProposal({
        kind: "merge_entities",
        entity_type: "tag",
        entity_id: 3,
        agent_payload: {
          kind: "merge_entities",
          entity_type: "tag",
          source_id: 3,
          target_id: 1,
        },
        base_snapshot: {
          source: { id: 3, name: "wichtig", document_count: 2 },
          target: { id: 1, name: "Rechnung", document_count: 40 },
        },
      }),
    );
    expect(await screen.findByText("wichtig")).toBeInTheDocument();
    expect(screen.getByText("Rechnung")).toBeInTheDocument();
    expect(screen.queryByText("source_id")).not.toBeInTheDocument();
  });
});
