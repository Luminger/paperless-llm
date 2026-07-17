import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import SessionDetail from "./SessionDetail";
import { renderWithProviders, makeProposal } from "../test/utils";
import { api, type SessionDetail as SessionDetailT, type TranscriptItem } from "../api";

function mkItem(overrides: Partial<TranscriptItem>): TranscriptItem {
  return {
    role: "agent",
    content: "",
    origin: "chat",
    tool_name: null,
    tool_args: null,
    tool_result: null,
    ...overrides,
  };
}

vi.mock("../api", () => ({
  api: {
    getSession: vi.fn(),
    getOcrReview: vi.fn(),
    resolveOcrGate: vi.fn(),
    rerunOcr: vi.fn(),
    sendMessage: vi.fn(),
    // ProposalCard dependencies:
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

function makeDetail(overrides: Partial<SessionDetailT> = {}): SessionDetailT {
  return {
    id: 9,
    agent_kind: "document",
    entity_type: "document",
    entity_id: 7,
    title: "Document #7 analysis",
    status: "idle",
    phase: "done",
    params: { redo_ocr: false },
    error: null,
    created_at: "2026-07-17T10:00:00Z",
    updated_at: "2026-07-17T10:01:00Z",
    proposal_count: 0,
    transcript: [
      mkItem({ role: "user", origin: "pipeline", content: "Process document id=7." }),
      mkItem({ role: "tool", tool_name: "get_document", tool_args: { document_id: 7 } }),
      mkItem({ role: "agent", content: "All done; proposed a better title." }),
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

describe("SessionDetail timeline", () => {
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
    mocked.listDocumentTypes.mockResolvedValue([{ id: 1, name: "Rechnung" }]);
    mocked.listStoragePaths.mockResolvedValue([]);
  });

  it("shows the OCR gate with a diff and resolves with user-fixed text", async () => {
    mocked.getSession.mockResolvedValue(
      makeDetail({ phase: "ocr_review", params: { redo_ocr: true } }),
    );
    mocked.getOcrReview.mockResolvedValue({
      document_id: 7,
      previous_content: "old garbled line",
      ocr_text: "clean OCR line",
      pages: 1,
    });
    mocked.resolveOcrGate.mockResolvedValue(makeDetail({ phase: "analyzing" }) as never);
    renderDetail();

    expect(await screen.findByText(/OCR review — your input needed/)).toBeInTheDocument();
    // Diff table shows both texts (word-diff splits them across spans).
    await waitFor(() => expect(document.body.textContent).toContain("old garbled line"));
    expect(document.body.textContent).toContain("clean OCR line");

    // Fix the text manually, then accept.
    await userEvent.click(await screen.findByRole("button", { name: "edit new text" }));
    const ta = screen.getByLabelText("new content");
    await userEvent.clear(ta);
    await userEvent.type(ta, "clean OCR line, fixed");
    await userEvent.click(screen.getByRole("button", { name: /Accept .*continue/ }));

    await waitFor(() =>
      expect(mocked.resolveOcrGate).toHaveBeenCalledWith(9, "clean OCR line, fixed"),
    );
  });

  it("keep-existing resolves the gate with null", async () => {
    mocked.getSession.mockResolvedValue(
      makeDetail({ phase: "ocr_review", params: { redo_ocr: true } }),
    );
    mocked.getOcrReview.mockResolvedValue({
      document_id: 7, previous_content: "a", ocr_text: "b", pages: 1,
    });
    mocked.resolveOcrGate.mockResolvedValue(makeDetail() as never);
    renderDetail();

    await userEvent.click(
      await screen.findByRole("button", { name: /Keep existing content/ }),
    );
    await waitFor(() => expect(mocked.resolveOcrGate).toHaveBeenCalledWith(9, null));
  });

  it("switches between side-by-side and unified diff", async () => {
    mocked.getSession.mockResolvedValue(
      makeDetail({ phase: "ocr_review", params: { redo_ocr: true } }),
    );
    mocked.getOcrReview.mockResolvedValue({
      document_id: 7, previous_content: "same\nremoved", ocr_text: "same\nadded", pages: 1,
    });
    renderDetail();

    expect(await screen.findByText("New content (OCR)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "unified" }));
    // Unified view collapses to a single column (no right-hand title).
    expect(screen.queryByText("New content (OCR)")).not.toBeInTheDocument();
    expect(localStorage.getItem("pllm.diffMode")).toBe("unified");
    // Both texts still visible in the unified rendering.
    expect(document.body.textContent).toContain("removed");
    expect(document.body.textContent).toContain("added");
  });

  it("renders the finished timeline with ALL proposals inline (metadata + create entity)", async () => {
    mocked.getSession.mockResolvedValue(
      makeDetail({
        phase: "done",
        params: { redo_ocr: true, ocr_gate: "accepted" },
        proposals: [
          makeProposal({ id: 1 }),
          makeProposal({
            id: 2,
            kind: "create_entity",
            agent_payload: {
              kind: "create_entity",
              entity_type: "correspondent",
              name: "Bei Spiel GmbH",
              reason: "Sender not in the list",
            },
          }),
        ],
      }),
    );
    renderDetail();

    expect(await screen.findByText(/new content accepted/)).toBeInTheDocument();
    expect(screen.getByText(/All done; proposed a better title/)).toBeInTheDocument();
    // Both proposals inline on the same page:
    expect(screen.getByText("update document metadata")).toBeInTheDocument();
    expect(screen.getByText("create entity")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Bei Spiel GmbH")).toBeInTheDocument();
    // Apply buttons per proposal.
    expect(screen.getAllByRole("button", { name: "Apply to paperless" })).toHaveLength(2);
  });

  it("shows 'no changes proposed' when the agent proposed nothing", async () => {
    mocked.getSession.mockResolvedValue(makeDetail({ proposals: [] }));
    renderDetail();
    expect(await screen.findByText("No changes proposed")).toBeInTheDocument();
  });

  it("shows failures on the analysis step", async () => {
    mocked.getSession.mockResolvedValue(
      makeDetail({ status: "failed", phase: "analyzing", error: "ModelAPIError: boom" }),
    );
    renderDetail();
    expect(await screen.findByText(/Metadata analysis failed/)).toBeInTheDocument();
    expect(screen.getByText(/boom/)).toBeInTheDocument();
  });

  it("offers an OCR re-run with instructions at the gate", async () => {
    mocked.getSession.mockResolvedValue(
      makeDetail({ phase: "ocr_review", params: { redo_ocr: true } }),
    );
    mocked.getOcrReview.mockResolvedValue({
      document_id: 7, previous_content: "a", ocr_text: "b", pages: 1,
    });
    mocked.rerunOcr.mockResolvedValue(
      makeDetail({ phase: "ocr_running" }) as never,
    );
    renderDetail();

    await userEvent.click(await screen.findByText(/Re-run it with instructions/));
    await userEvent.type(
      screen.getByLabelText("re-run instructions"),
      "mind the stamp",
    );
    await userEvent.click(screen.getByRole("button", { name: "Re-run OCR" }));
    await waitFor(() =>
      expect(mocked.rerunOcr).toHaveBeenCalledWith(9, "mind the stamp"),
    );
  });

  it("shows the conversation with tool trace and sends steering messages", async () => {
    mocked.getSession.mockResolvedValue(
      makeDetail({
        transcript: [
          mkItem({ role: "user", origin: "pipeline", content: "Process document id=7." }),
          mkItem({ role: "agent", content: "Initial analysis summary." }),
          mkItem({ role: "user", content: "Please use the German title" }),
          mkItem({ role: "tool", tool_name: "propose_update_document_metadata" }),
          mkItem({ role: "agent", content: "Done — revised the proposal." }),
        ],
      }),
    );
    mocked.sendMessage.mockResolvedValue(makeDetail({ status: "running" }) as never);
    renderDetail();

    expect(await screen.findByText("Please use the German title")).toBeInTheDocument();
    expect(screen.getByText("Done — revised the proposal.")).toBeInTheDocument();
    expect(screen.getByText(/1 tool call/)).toBeInTheDocument();

    await userEvent.type(
      screen.getByLabelText("steer the agent"),
      "also add the invoice tag",
    );
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(mocked.sendMessage).toHaveBeenCalledWith(9, "also add the invoice tag"),
    );
  });

  it("disables the composer and shows progress while the agent runs", async () => {
    mocked.getSession.mockResolvedValue(makeDetail({ status: "running" }));
    renderDetail();
    expect(await screen.findByText(/Agent is working…/)).toBeInTheDocument();
    expect(screen.getByLabelText("steer the agent")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("collapses superseded revisions under the current proposal", async () => {
    mocked.getSession.mockResolvedValue(
      makeDetail({
        proposals: [
          makeProposal({ id: 1, status: "superseded", revision: 1 }),
          makeProposal({
            id: 2,
            revision: 2,
            supersedes_id: 1,
            agent_payload: {
              kind: "update_document_metadata",
              document_id: 7,
              reason: "r",
              title: "Deutscher Titel",
            },
          }),
        ],
      }),
    );
    renderDetail();

    expect(await screen.findByText(/1 earlier revision/)).toBeInTheDocument();
    // Only one Apply button — the superseded revision is read-only.
    expect(screen.getAllByRole("button", { name: "Apply to paperless" })).toHaveLength(1);
  });
});
