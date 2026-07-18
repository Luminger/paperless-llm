import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import SessionDetail from "./SessionDetail";
import { makeEntity, makeProposal, renderWithProviders } from "../test/utils";
import {
  api,
  type SessionDetail as SessionDetailT,
  type Step,
  type TranscriptItem,
} from "../api";

vi.mock("../api", () => ({
  api: {
    getSession: vi.fn(),
    getOcrReview: vi.fn(),
    resolveStep: vi.fn(),
    retryStep: vi.fn(),
    redoStep: vi.fn(),
    sendMessage: vi.fn(),
    unarchiveSession: vi.fn(),
    // ProposalCard dependencies:
    patchProposal: vi.fn(),
    revertCheck: vi.fn(),
    proposalAction: vi.fn(),
    getDocument: vi.fn(),
    listTags: vi.fn(),
    listCorrespondents: vi.fn(),
    listDocumentTypes: vi.fn(),
    listStoragePaths: vi.fn(),
    getJob: vi.fn(),
    getJobAttention: vi.fn(),
  },
}));
const mocked = vi.mocked(api);

beforeEach(() => {
  mocked.revertCheck.mockResolvedValue({ revert_noop: false });
});

function mkItem(overrides: Partial<TranscriptItem>): TranscriptItem {
  return {
    role: "agent",
    content: "",
    origin: "chat",
    tool_name: null,
    tool_args: null,
    tool_result: null,
    timing: null,
    ts: null,
    ...overrides,
  } as TranscriptItem;
}

let stepSeq = 100;
function mkStep(overrides: Partial<Step>): Step {
  return {
    id: ++stepSeq,
    session_id: 9,
    kind: "analysis",
    state: "succeeded",
    lane: "interactive",
    input: {},
    result: {},
    error: null,
    attempts: [],
    attempt_count: 1,
    max_attempts: 3,
    scheduled_at: null,
    supersedes_id: null,
    created_at: "2026-07-17T10:00:00Z",
    started_at: "2026-07-17T10:00:05Z",
    finished_at: "2026-07-17T10:01:00Z",
    transcript: [],
    ...overrides,
  };
}

function makeDetail(overrides: Partial<SessionDetailT> = {}): SessionDetailT {
  return {
    id: 9,
    agent_kind: "document",
    entity_type: "document",
    entity_id: 7,
    entity_name: "",
    title: "Document #7 analysis",
    status: "idle",
    phase: "done",
    params: { redo_ocr: false },
    error: null,
    archived_at: null,
    created_at: "2026-07-17T10:00:00Z",
    updated_at: "2026-07-17T10:01:00Z",
    proposal_count: 0,
    pending_proposal_count: 0,
    applied_proposal_count: 0,
    steps: [
      mkStep({
        kind: "analysis",
        state: "succeeded",
        result: { message_range: [0, 2], proposal_ids: [] },
        transcript: [
          mkItem({ role: "user", origin: "pipeline", content: "Process document id=7." }),
          mkItem({ role: "tool", tool_name: "get_document", tool_args: { document_id: 7 } }),
          mkItem({ role: "agent", content: "All done; proposed a better title." }),
        ],
      }),
    ],
    proposals: [],
    ...overrides,
  };
}

function renderDetail() {
  return renderWithProviders(<SessionDetail />, {
    route: "/sessions/9",
    path: "/sessions/:id",
  });
}

describe("SessionDetail step feed", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mocked.getDocument.mockResolvedValue({
      id: 7, title: "scan_0001", correspondent: null, document_type: null,
      storage_path: null, tags: [], created: "2024-04-17", added: null,
      archive_serial_number: null,
    });
    mocked.listTags.mockResolvedValue([]);
    mocked.listCorrespondents.mockResolvedValue([]);
    mocked.listDocumentTypes.mockResolvedValue([makeEntity({ id: 1, name: "Rechnung" })]);
    mocked.listStoragePaths.mockResolvedValue([]);
  });

  it("renders the OCR gate with a diff and resolves the awaiting step", async () => {
    const gate = mkStep({ kind: "ocr", state: "awaiting_user", result: { pages: 1 } });
    mocked.getSession.mockResolvedValue(
      makeDetail({ phase: "ocr_review", params: { redo_ocr: true }, steps: [gate] }),
    );
    mocked.getOcrReview.mockResolvedValue({
      document_id: 7,
      previous_content: "old garbled line",
      ocr_text: "clean OCR line",
      pages: 1,
      timings: [],
    });
    mocked.resolveStep.mockResolvedValue(mkStep({ kind: "ocr", state: "succeeded" }));
    renderDetail();

    expect(await screen.findByText("your input needed")).toBeInTheDocument();
    await waitFor(() => expect(document.body.textContent).toContain("old garbled line"));

    await userEvent.click(await screen.findByRole("button", { name: "edit new text" }));
    const ta = screen.getByLabelText("new content");
    await userEvent.clear(ta);
    await userEvent.type(ta, "clean OCR line, fixed");
    await userEvent.click(screen.getByRole("button", { name: /Accept .*continue/ }));

    await waitFor(() =>
      expect(mocked.resolveStep).toHaveBeenCalledWith(9, gate.id, "clean OCR line, fixed"),
    );
  });

  it("keep-existing resolves with null; gate redo sends amended input", async () => {
    const gate = mkStep({ kind: "ocr", state: "awaiting_user" });
    mocked.getSession.mockResolvedValue(
      makeDetail({ phase: "ocr_review", params: { redo_ocr: true }, steps: [gate] }),
    );
    mocked.getOcrReview.mockResolvedValue({
      document_id: 7, previous_content: "a", ocr_text: "b", pages: 1, timings: [],
    });
    mocked.resolveStep.mockResolvedValue(mkStep({}));
    mocked.redoStep.mockResolvedValue(mkStep({ kind: "ocr", state: "pending" }));
    renderDetail();

    await userEvent.click(
      await screen.findByRole("button", { name: /Keep existing content/ }),
    );
    await waitFor(() => expect(mocked.resolveStep).toHaveBeenCalledWith(9, gate.id, null));

    await userEvent.click(screen.getByText(/Re-run it with instructions/));
    await userEvent.type(screen.getByLabelText("re-run instructions"), "mind the stamp");
    await userEvent.click(screen.getByRole("button", { name: "Re-run OCR" }));
    await waitFor(() =>
      expect(mocked.redoStep).toHaveBeenCalledWith(9, gate.id, {
        instructions: "mind the stamp",
      }),
    );
  });

  it("superseded steps collapse but stay inspectable: params, output, diff", async () => {
    const first = mkStep({
      kind: "ocr",
      state: "superseded",
      input: { instructions: "first try" },
      result: { pages: 1, text: "the old OCR output", previous_content: "paperless text then" },
    });
    const second = mkStep({
      kind: "ocr",
      state: "succeeded",
      input: { instructions: "mind the stamp" },
      supersedes_id: first.id,
      result: { pages: 1, duration_s: 12.3, resolution: "accepted" },
    });
    mocked.getSession.mockResolvedValue(makeDetail({ steps: [first, second, makeDetail().steps[0]] }));
    renderDetail();

    expect(await screen.findByText("superseded")).toBeInTheDocument();
    // Collapsed summary shows the parameters it ran with.
    expect(screen.getByText(/instructions: “first try”/)).toBeInTheDocument();
    // The LIVE run renders its guidance the way agent turns do.
    expect(screen.getByText("Your instructions")).toBeInTheDocument();
    expect(screen.getByText("mind the stamp")).toBeInTheDocument();
    // Expanding the superseded run reveals ITS diff (read-only).
    await userEvent.click(screen.getByText(/instructions: “first try”/));
    await waitFor(() => expect(document.body.textContent).toContain("the old OCR output"));
    expect(document.body.textContent).toContain("paperless text then");
    expect(screen.queryByRole("button", { name: "edit new text" })).not.toBeInTheDocument();
  });

  it("renders proposals inside their step and no-changes note", async () => {
    const analysis = mkStep({
      kind: "analysis",
      state: "succeeded",
      result: { message_range: [0, 1], proposal_ids: [1, 2] },
      transcript: [mkItem({ role: "agent", content: "Summary here." })],
    });
    mocked.getSession.mockResolvedValue(
      makeDetail({
        steps: [analysis],
        proposals: [
          makeProposal({ id: 1 }),
          makeProposal({
            id: 2,
            kind: "create_entity",
            agent_payload: {
              kind: "create_entity",
              entity_type: "correspondent",
              name: "Bei Spiel GmbH",
            },
          }),
        ],
      }),
    );
    renderDetail();

    expect(await screen.findByText("Summary here.")).toBeInTheDocument();
    expect(screen.getByText("update document metadata")).toBeInTheDocument();
    // Proposals name the entity type they touch.
    expect(screen.getByText("create correspondent")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Apply to paperless" })).toHaveLength(2);
  });

  it("failed steps show error, attempt history and unlimited Retry now", async () => {
    const failed = mkStep({
      kind: "analysis",
      state: "failed",
      error: "ConnectError: LLM down",
      attempt_count: 3,
      attempts: [
        { attempt: 1, started_at: "2026-07-17T10:00:00Z", finished_at: "2026-07-17T10:00:30Z", error: "ConnectError: LLM down" },
        { attempt: 2, started_at: "2026-07-17T10:01:00Z", finished_at: "2026-07-17T10:01:30Z", error: "ConnectError: LLM down" },
        { attempt: 3, started_at: "2026-07-17T10:02:00Z", finished_at: "2026-07-17T10:02:30Z", error: "ConnectError: LLM down" },
      ],
    });
    mocked.getSession.mockResolvedValue(
      makeDetail({ status: "failed", phase: "analyzing", steps: [failed] }),
    );
    mocked.retryStep.mockResolvedValue(mkStep({ state: "pending" }));
    renderDetail();

    expect(await screen.findByText("failed")).toBeInTheDocument();
    expect(screen.getByText("Attempt 1")).toBeInTheDocument();
    expect(screen.getByText("Attempt 3")).toBeInTheDocument();
    expect(screen.getByText(/2 auto-retries used/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry now" }));
    await waitFor(() => expect(mocked.retryStep).toHaveBeenCalledWith(9, failed.id));
  });

  it("scheduled retries show the plan and Retry now skips the backoff", async () => {
    const scheduled = mkStep({
      kind: "chat",
      state: "pending",
      attempt_count: 1,
      scheduled_at: "2026-07-17T18:00:00Z",
      attempts: [{ attempt: 1, started_at: "2026-07-17T17:58:00Z", finished_at: "2026-07-17T17:59:00Z", error: "boom" }],
    });
    mocked.getSession.mockResolvedValue(makeDetail({ steps: [scheduled] }));
    renderDetail();
    expect(await screen.findByText(/retry scheduled/)).toBeInTheDocument();
    expect(screen.getByText(/auto-retry 1\/2 at/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry now" })).toBeInTheDocument();
  });

  it("chat: the NEXT TURN box appears once work is settled and sends", async () => {
    mocked.getSession.mockResolvedValue(makeDetail({}));
    mocked.sendMessage.mockResolvedValue(mkStep({ kind: "chat", state: "pending" }));
    renderDetail();

    // The coming turn renders as its own box; the input sits where the
    // user prompt will appear.
    await userEvent.type(
      await screen.findByLabelText("steer the agent"),
      "also add the invoice tag",
    );
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(mocked.sendMessage).toHaveBeenCalledWith(9, "also add the invoice tag"),
    );
    // The textbox transformed into the sent text.
    expect(screen.getByText("also add the invoice tag")).toBeInTheDocument();
  });

  it("no continue affordance while a step runs", async () => {
    mocked.getSession.mockResolvedValue(
      makeDetail({
        status: "running",
        steps: [mkStep({ kind: "analysis", state: "running" })],
      }),
    );
    renderDetail();
    expect(await screen.findByText("running…")).toBeInTheDocument();
    expect(screen.queryByLabelText("steer the agent")).not.toBeInTheDocument();
  });

  it("chat turns render user bubble, tool trace and reply with metrics", async () => {
    const timing = {
      started_at: "2026-07-17T10:00:00Z",
      finished_at: "2026-07-17T10:00:03Z",
      duration_s: 5.0,
      ttft_s: 0.42,
      input_tokens: 900,
      output_tokens: 130,
      tps: 41.3,
    };
    const chat = mkStep({
      kind: "chat",
      state: "succeeded",
      input: { content: "Please use the German title" },
      result: { message_range: [2, 5], proposal_ids: [] },
      transcript: [
        mkItem({ role: "user", content: "Please use the German title" }),
        mkItem({ role: "tool", tool_name: "propose_update_document_metadata", timing }),
        mkItem({ role: "agent", content: "Done — revised the proposal.", timing }),
      ],
    });
    mocked.getSession.mockResolvedValue(makeDetail({ steps: [...makeDetail().steps, chat] }));
    renderDetail();

    expect(await screen.findByText("Please use the German title")).toBeInTheDocument();
    expect(screen.getByText("Done — revised the proposal.")).toBeInTheDocument();
    expect(screen.getAllByText(/5\.0s · 41 tok\/s · ttft 0\.42s/).length).toBeGreaterThan(0);
    // The tool call is a first-class collapsed row.
    expect(
      screen.getByText("propose_update_document_metadata"),
    ).toBeInTheDocument();
  });

  it("Redo asks how the redo should run and warns about superseding", async () => {
    const detail = makeDetail({});
    mocked.getSession.mockResolvedValue(detail);
    mocked.redoStep.mockResolvedValue(mkStep({ state: "pending" }));
    renderDetail();

    await userEvent.click(await screen.findByRole("button", { name: "Redo…" }));
    // The dialog warns that downstream steps get superseded.
    expect(screen.getByText(/every step after it/)).toBeInTheDocument();
    await userEvent.type(
      screen.getByLabelText("redo instructions for the agent"),
      "focus on the dates",
    );
    await userEvent.click(screen.getByRole("button", { name: "Redo step" }));
    await waitFor(() =>
      expect(mocked.redoStep).toHaveBeenCalledWith(9, detail.steps[0].id, {
        instructions: "focus on the dates",
      }),
    );
  });
});

describe("SessionDetail — archive & breadcrumb", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listTags.mockResolvedValue([]);
    mocked.listCorrespondents.mockResolvedValue([]);
    mocked.listDocumentTypes.mockResolvedValue([]);
    mocked.listStoragePaths.mockResolvedValue([]);
    mocked.getDocument.mockResolvedValue({
      id: 7, title: "scan_0001", correspondent: null, document_type: null,
      storage_path: null, tags: [], created: null, added: null,
      archive_serial_number: null,
    });
  });

  it("has a breadcrumb to the entity page", async () => {
    mocked.getSession.mockResolvedValue(makeDetail({}));
    renderDetail();
    const crumb = await screen.findByRole("link", { name: /← Back to the document/ });
    expect(crumb.getAttribute("href")).toBe("/documents/7");
  });

  it("archived sessions: banner, no apply, revert still offered", async () => {
    mocked.getSession.mockResolvedValue(
      makeDetail({
        archived_at: "2026-07-17T12:00:00Z",
        steps: [
          mkStep({
            kind: "analysis",
            state: "succeeded",
            result: { message_range: [0, 1], proposal_ids: [1, 2] },
            transcript: [mkItem({ role: "agent", content: "Summary." })],
          }),
        ],
        proposals: [
          makeProposal({ id: 1 }),
          makeProposal({ id: 2, status: "applied", applied: true }),
        ],
      }),
    );
    renderDetail();

    expect(await screen.findByText(/going back in time is/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unarchive" })).toBeInTheDocument();
    // Pending proposal: no Apply, explanatory note instead.
    expect(screen.queryByRole("button", { name: "Apply to paperless" })).not.toBeInTheDocument();
    expect(screen.getByText(/unarchive the session first/)).toBeInTheDocument();
    // Applied proposal: revert remains available (back in time is fine).
    expect(screen.getByRole("button", { name: "Revert" })).toBeInTheDocument();
    // No next-turn box on archived sessions.
    expect(screen.queryByLabelText("steer the agent")).not.toBeInTheDocument();
  });
});

describe("Transcript — first-class model exchange", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listTags.mockResolvedValue([]);
    mocked.listCorrespondents.mockResolvedValue([]);
    mocked.listDocumentTypes.mockResolvedValue([]);
    mocked.listStoragePaths.mockResolvedValue([]);
  });

  it("thinking blocks are shown collapsed and expandable", async () => {
    const chat = mkStep({
      kind: "chat",
      state: "succeeded",
      result: { proposal_ids: [] },
      transcript: [
        mkItem({ role: "thinking", content: "Let me check the correspondent list first." }),
        mkItem({ role: "agent", content: "Here is my answer." }),
      ],
    });
    mocked.getSession.mockResolvedValue(makeDetail({ steps: [chat] }));
    renderDetail();

    const trigger = await screen.findByText("Reasoning");
    // Collapsed: only the one-line teaser, no expanded body yet.
    await userEvent.click(trigger);
    expect(
      screen.getAllByText(/Let me check the correspondent list first\./).length,
    ).toBeGreaterThan(0);
  });

  it("tool calls expand to full arguments and the complete return value", async () => {
    const chat = mkStep({
      kind: "chat",
      state: "succeeded",
      result: { proposal_ids: [] },
      transcript: [
        mkItem({
          role: "tool",
          tool_name: "get_document",
          tool_args: { document_id: 7 },
          tool_result: '{"id": 7} …[truncated]',
          tool_result_full: { id: 7, title: "Kraxi Rechnung", content: "Sehr geehrte…" },
        } as never),
      ],
    });
    mocked.getSession.mockResolvedValue(makeDetail({ steps: [chat] }));
    renderDetail();

    await userEvent.click(await screen.findByText("get_document"));
    expect(screen.getByText("Arguments")).toBeInTheDocument();
    expect(screen.getByText(/"document_id": 7/)).toBeInTheDocument();
    expect(screen.getByText("Returned")).toBeInTheDocument();
    // The COMPLETE value, not the truncated summary.
    expect(screen.getByText(/Kraxi Rechnung/)).toBeInTheDocument();
  });

  it("rejected tool calls are marked and show the rejection reason", async () => {
    const chat = mkStep({
      kind: "chat",
      state: "succeeded",
      result: { proposal_ids: [] },
      transcript: [
        mkItem({
          role: "tool",
          tool_name: "propose_update_document_metadata",
          tool_args: {},
          tool_result: "rejected: no-op proposal",
          tool_result_full: "no-op proposal",
          tool_rejected: true,
        } as never),
      ],
    });
    mocked.getSession.mockResolvedValue(makeDetail({ steps: [chat] }));
    renderDetail();

    expect(await screen.findByText("rejected")).toBeInTheDocument();
    await userEvent.click(screen.getByText("propose_update_document_metadata"));
    expect(screen.getByText("Rejected with")).toBeInTheDocument();
    expect(screen.getByText(/no-op proposal/)).toBeInTheDocument();
  });
});

describe("Finished turns fold their work", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listTags.mockResolvedValue([]);
    mocked.listCorrespondents.mockResolvedValue([]);
    mocked.listDocumentTypes.mockResolvedValue([]);
    mocked.listStoragePaths.mockResolvedValue([]);
  });

  it("reasoning + tool calls collapse; the summary stays fixed", async () => {
    const chat = mkStep({
      kind: "chat",
      state: "succeeded",
      result: { proposal_ids: [] },
      transcript: [
        mkItem({ role: "user", content: "please check the tags" }),
        mkItem({ role: "thinking", content: "Let me look at the tags." }),
        mkItem({ role: "tool", tool_name: "list_tags", tool_args: {} }),
        mkItem({ role: "agent", content: "All tags are fine." }),
      ],
    });
    mocked.getSession.mockResolvedValue(makeDetail({ steps: [chat] }));
    renderDetail();

    // The user's message and the final summary are fixed parts.
    expect(await screen.findByText("please check the tags")).toBeInTheDocument();
    expect(screen.getByText("All tags are fine.")).toBeInTheDocument();
    // The work is folded behind one collapsed section with counts.
    expect(screen.getByText("The agent's work")).toBeInTheDocument();
    expect(screen.getByText(/1 tool call · 1 reasoning step/)).toBeInTheDocument();
  });
});

describe("Chronological turn rendering", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listTags.mockResolvedValue([]);
    mocked.listCorrespondents.mockResolvedValue([]);
    mocked.listDocumentTypes.mockResolvedValue([]);
    mocked.listStoragePaths.mockResolvedValue([]);
    mocked.getDocument.mockResolvedValue({
      id: 7, title: "scan_0001", correspondent: null, document_type: null,
      storage_path: null, tags: [], created: "2024-04-17", added: null,
      archive_serial_number: null,
    });
    mocked.revertCheck.mockResolvedValue({ revert_noop: false });
  });

  it("the proposal renders in place of its propose_* tool call", async () => {
    const step = mkStep({
      kind: "analysis",
      state: "succeeded",
      result: { proposal_ids: [5] },
      transcript: [
        mkItem({ role: "thinking", content: "check the doc" }),
        mkItem({ role: "tool", tool_name: "get_document", tool_args: { document_id: 7 } }),
        mkItem({
          role: "tool",
          tool_name: "propose_update_document_metadata",
          tool_args: { document_id: 7, title: "Better" },
          tool_result: "Proposal #5 (update_document_metadata) recorded for human review.",
        }),
        mkItem({ role: "agent", content: "Proposed a better title." }),
      ],
    });
    mocked.getSession.mockResolvedValue(
      makeDetail({
        steps: [step],
        proposals: [makeProposal({ id: 5 })],
      }),
    );
    renderDetail();

    // The propose call is NOT a trace row — the proposal stands in
    // its place; the fold only counts the exploratory work.
    // The proposal card is present (id-free header).
    expect((await screen.findAllByText("Proposal")).length).toBeGreaterThan(0);
    expect(screen.getByText("The agent's work")).toBeInTheDocument();
    expect(screen.getByText(/1 tool call · 1 reasoning step/)).toBeInTheDocument();
    expect(screen.getByText("Proposed a better title.")).toBeInTheDocument();
  });

  it("a turn whose proposals were all revised away folds entirely", async () => {
    const analysis = mkStep({
      kind: "analysis",
      state: "succeeded",
      result: { proposal_ids: [1] },
      transcript: [mkItem({ role: "agent", content: "Old summary." })],
    });
    mocked.getSession.mockResolvedValue(
      makeDetail({
        steps: [analysis],
        proposals: [makeProposal({ id: 1, status: "superseded" })],
      }),
    );
    renderDetail();

    expect(
      await screen.findByText("superseded by a later revision"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/its proposals were revised in a later turn — expand to inspect/),
    ).toBeInTheDocument();
    // The old summary is folded away until expanded.
    await userEvent.click(
      screen.getByText(/its proposals were revised in a later turn/),
    );
    expect(screen.getByText("Old summary.")).toBeInTheDocument();
  });
});

describe("Reference tokens", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listTags.mockResolvedValue([
      makeEntity({ id: 5, name: "steuer", document_count: 3 }),
    ]);
    mocked.listCorrespondents.mockResolvedValue([]);
    mocked.listDocumentTypes.mockResolvedValue([]);
    mocked.listStoragePaths.mockResolvedValue([]);
    mocked.getDocument.mockResolvedValue({
      id: 13, title: "Campus Media Statement", correspondent: null,
      document_type: null, storage_path: null, tags: [], created: "1971-03-01",
      added: null, archive_serial_number: null,
    });
  });

  it("expands [[type:id]] tokens in the summary into linked chips", async () => {
    const step = mkStep({
      kind: "analysis",
      state: "succeeded",
      result: { proposal_ids: [] },
      transcript: [
        mkItem({
          role: "agent",
          content: "Assigned [[tag:5]] to [[document:13]].",
        }),
      ],
    });
    mocked.getSession.mockResolvedValue(makeDetail({ steps: [step] }));
    renderDetail();

    // Names, not tokens — rendered as links to the entity pages.
    const tagChip = await screen.findByRole("link", { name: "steuer" });
    expect(tagChip.getAttribute("href")).toBe("/taxonomy/tag/5");
    const docChip = await screen.findByRole("link", { name: "Campus Media Statement" });
    expect(docChip.getAttribute("href")).toBe("/documents/13");
    expect(screen.queryByText(/\[\[tag:5\]\]/)).not.toBeInTheDocument();
  });

  it("unknown token types fall back to the literal text", async () => {
    const step = mkStep({
      kind: "analysis",
      state: "succeeded",
      result: { proposal_ids: [] },
      transcript: [mkItem({ role: "agent", content: "See [[wormhole:9]]." })],
    });
    mocked.getSession.mockResolvedValue(makeDetail({ steps: [step] }));
    renderDetail();
    expect(await screen.findByText(/\[\[wormhole:9\]\]/)).toBeInTheDocument();
  });
});

describe("SessionDetail job flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mocked.revertCheck.mockResolvedValue({ revert_noop: false });
    mocked.getSession.mockResolvedValue(makeDetail({ job_id: 3 }));
    mocked.getJob.mockResolvedValue({
      id: 3,
      kind: "bulk_analyze",
      params: { label: "Inbox" },
      status: "running",
      total: 5,
      done: 2,
      failed: 0,
      created_at: "2026-07-17T10:00:00Z",
      updated_at: "2026-07-17T10:00:00Z",
      sessions: [],
    } as never);
  });

  it("shows the flow bar with Next when in flow mode", async () => {
    mocked.getJobAttention.mockResolvedValue({ next_session_id: 12, remaining: 3 });
    renderWithProviders(<SessionDetail />, {
      route: "/sessions/9?flow=1",
      path: "/sessions/:id",
    });
    expect(await screen.findByText(/3 waiting on you/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /next/i })).toBeInTheDocument();
    expect(mocked.getJobAttention).toHaveBeenCalledWith(3, 9);
  });

  it("offers the way back when nothing else waits", async () => {
    mocked.getJobAttention.mockResolvedValue({ next_session_id: null, remaining: 0 });
    renderWithProviders(<SessionDetail />, {
      route: "/sessions/9?flow=1",
      path: "/sessions/:id",
    });
    expect(await screen.findByText(/nothing else is waiting on you/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /back to the job/i })).toBeInTheDocument();
  });

  it("stays hidden outside flow mode", async () => {
    mocked.getJobAttention.mockResolvedValue({ next_session_id: 12, remaining: 3 });
    renderDetail();
    await screen.findByText("All done; proposed a better title.");
    expect(screen.queryByText(/waiting on you/)).toBeNull();
  });
});
