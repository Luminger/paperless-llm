import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import Documents from "./Documents";
import { renderWithProviders } from "../test/utils";
import { api } from "../api";

vi.mock("../api", () => ({
  api: { listDocuments: vi.fn(), analyzeDocument: vi.fn() },
}));
const listDocuments = vi.mocked(api.listDocuments);
const analyzeDocument = vi.mocked(api.analyzeDocument);

const doc = {
  id: 17,
  title: "scan_0001",
  correspondent: 2,
  document_type: null,
  storage_path: null,
  tags: [6],
  created: "2024-04-17",
  added: "2026-07-17",
  archive_serial_number: null,
};

describe("Documents", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("lists documents and searches", async () => {
    listDocuments.mockResolvedValue({ count: 1, results: [doc] });
    renderWithProviders(<Documents />);

    expect(await screen.findByText("scan_0001")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText(/Full-text search/), "Rechnung");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() =>
      expect(listDocuments).toHaveBeenLastCalledWith("Rechnung"),
    );
  });

  it("analyze opens the dialog; the OCR flag and instructions are passed", async () => {
    listDocuments.mockResolvedValue({ count: 1, results: [doc] });
    analyzeDocument.mockResolvedValue({ id: 9 } as never);
    renderWithProviders(<Documents />);
    await screen.findByText("scan_0001");

    await userEvent.click(screen.getByRole("button", { name: "Analyze" }));
    await userEvent.click(screen.getByRole("checkbox")); // enable re-do OCR
    await userEvent.type(
      screen.getByPlaceholderText(/Optional instructions/),
      "focus on the date",
    );
    await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));

    await waitFor(() =>
      expect(analyzeDocument).toHaveBeenCalledWith(17, {
        redo_ocr: true,
        instructions: "focus on the date",
      }),
    );
  });

  it("surfaces analyze errors", async () => {
    listDocuments.mockResolvedValue({ count: 1, results: [doc] });
    analyzeDocument.mockRejectedValue(new Error("503: llm endpoint down"));
    renderWithProviders(<Documents />);
    await screen.findByText("scan_0001");

    await userEvent.click(screen.getByRole("button", { name: "Analyze" }));
    await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
    expect(await screen.findByText(/llm endpoint down/)).toBeInTheDocument();
  });
});
