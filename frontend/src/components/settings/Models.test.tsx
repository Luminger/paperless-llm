// The runtime-editable model config: drafts, saving, source badges,
// env locks, the anti-loop sampling levers with their hover hints, and
// the connectivity/capability probes.

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { ModelsConfig } from "./Models";
import { renderWithProviders } from "../../test/utils";
import { api, type ConfigRow } from "../../api";

vi.mock("../../api", () => ({
  api: {
    getConfig: vi.fn(),
    putConfig: vi.fn(),
    testLlm: vi.fn(),
    detectLlm: vi.fn(),
  },
}));
let role = "admin";
vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ user: "simon", role }),
}));
const mocked = vi.mocked(api);

function row(key: string, over: Partial<ConfigRow> = {}): ConfigRow {
  return {
    key,
    value: null,
    editable: true,
    secret: false,
    source: "default",
    is_set: false,
    ...over,
  };
}

const ROWS: ConfigRow[] = [
  row("llm.agent.base_url", { value: "http://llm:8000/v1", source: "file" }),
  row("llm.agent.model", { value: "qwen3", source: "environment", editable: false }),
  row("llm.agent.api_key", { secret: true, is_set: true }),
  row("llm.ocr.sampling.temperature", { source: "default" }),
  row("llm.ocr.sampling.max_tokens", { value: 4000, source: "ui" }),
  row("queue.max_concurrent", { value: 2, source: "file" }),
];

const card = (title: string) =>
  within(screen.getByText(title).closest("[data-slot=card]") as HTMLElement);

describe("Settings — Models config", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    role = "admin";
    mocked.getConfig.mockResolvedValue(ROWS);
  });

  it("groups rows with human labels and source badges", async () => {
    renderWithProviders(<ModelsConfig />);
    expect(await screen.findByText("Agent model")).toBeInTheDocument();
    expect(screen.getByText("OCR model")).toBeInTheDocument();
    expect(screen.getByText("Behavior")).toBeInTheDocument();
    // Human labels, not raw keys.
    expect(card("Agent model").getByText("Endpoint")).toBeInTheDocument();
    expect(card("OCR model").getByText("Temperature")).toBeInTheDocument();
    expect(
      card("Behavior").getByText("Max concurrent requests"),
    ).toBeInTheDocument();
    // Provenance badges.
    expect(screen.getAllByText("config file").length).toBeGreaterThan(0);
    expect(screen.getByText("environment")).toBeInTheDocument();
  });

  it("environment-sourced keys are locked (disabled input)", async () => {
    renderWithProviders(<ModelsConfig />);
    expect(await screen.findByLabelText("llm.agent.model")).toBeDisabled();
    expect(screen.getByLabelText("llm.agent.base_url")).toBeEnabled();
  });

  it("secret keys never echo the value, only a set-marker placeholder", async () => {
    renderWithProviders(<ModelsConfig />);
    const secret = await screen.findByLabelText("llm.agent.api_key");
    expect(secret).toHaveAttribute("type", "password");
    expect(secret).toHaveAttribute(
      "placeholder",
      expect.stringContaining("set — type to replace"),
    );
    expect(secret).toHaveValue("");
  });

  it("sampling levers explain themselves on label hover", async () => {
    renderWithProviders(<ModelsConfig />);
    await userEvent.hover(await screen.findByText("Temperature"));
    expect(
      (await screen.findAllByText(/most loop-prone/)).length,
    ).toBeGreaterThan(0);
  });

  it("editing a sampling lever saves the typed number", async () => {
    mocked.putConfig.mockResolvedValue(ROWS);
    renderWithProviders(<ModelsConfig />);
    const save = await screen.findByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled(); // nothing dirty yet
    await userEvent.type(screen.getByLabelText("llm.ocr.sampling.temperature"), "0.2");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(mocked.putConfig).toHaveBeenCalledWith({
        "llm.ocr.sampling.temperature": 0.2,
      }),
    );
    // Saved: the draft is gone, the button disarms.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled(),
    );
  });

  it("emptying a numeric lever clears the override (null), not \"\"", async () => {
    mocked.putConfig.mockResolvedValue(ROWS);
    renderWithProviders(<ModelsConfig />);
    const input = await screen.findByLabelText("llm.ocr.sampling.max_tokens");
    await userEvent.clear(input);
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(mocked.putConfig).toHaveBeenCalledWith({
        "llm.ocr.sampling.max_tokens": null,
      }),
    );
  });

  it("ui-sourced values offer reset; Discard drops all drafts", async () => {
    renderWithProviders(<ModelsConfig />);
    // The ui override row swaps its badge for a reset affordance.
    await userEvent.click(await screen.findByRole("button", { name: "reset" }));
    // reset stages null — the save bar arms.
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(mocked.putConfig).not.toHaveBeenCalled();
  });

  it("Test connection reports reachability and latency", async () => {
    mocked.testLlm.mockResolvedValue({
      ok: true,
      base_url: "http://llm:8000/v1",
      model: "qwen3",
      latency_ms: 812,
      reply: "OK",
    });
    renderWithProviders(<ModelsConfig />);
    await screen.findByText("Agent model");
    await userEvent.click(
      card("Agent model").getByRole("button", { name: "Test connection" }),
    );
    expect(mocked.testLlm).toHaveBeenCalledWith("agent");
    expect(
      await screen.findByText(/✓ qwen3 reachable · 812 ms · “OK”/),
    ).toBeInTheDocument();
  });

  it("a failing probe shows the error, not a fake checkmark", async () => {
    mocked.testLlm.mockResolvedValue({
      ok: false,
      base_url: "http://llm:8000/v1",
      model: "qwen3",
      error: "connection refused",
    });
    renderWithProviders(<ModelsConfig />);
    await screen.findByText("Agent model");
    await userEvent.click(
      card("Agent model").getByRole("button", { name: "Test connection" }),
    );
    expect(await screen.findByText(/✗ connection refused/)).toBeInTheDocument();
  });

  it("Autodetect fills suggestions into the FORM as a reviewable draft", async () => {
    mocked.detectLlm.mockResolvedValue({
      base_url: "http://llm:8000/v1",
      model: "qwen3-vl",
      context_length: 32768,
      context_source: "vllm",
      max_images: 8,
      max_images_exact: true,
      tokens_per_image: 1200,
      images_in_context: 20,
      render_dpi: 150,
      suggestions: { "llm.ocr.sampling.max_tokens": 6000 },
    });
    renderWithProviders(<ModelsConfig />);
    await screen.findByText("OCR model");
    await userEvent.click(
      card("OCR model").getByRole("button", { name: "Autodetect" }),
    );
    expect(mocked.detectLlm).toHaveBeenCalledWith("ocr");
    // Findings + the review reminder…
    expect(
      await screen.findByText(/context window 32,768 \(vllm\)/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/suggestion filled into the form, review & save/),
    ).toBeInTheDocument();
    // …and the suggested value sits in the input as an UNSAVED draft.
    expect(screen.getByLabelText("llm.ocr.sampling.max_tokens")).toHaveValue(6000);
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
    expect(mocked.putConfig).not.toHaveBeenCalled();
  });

  it("non-admins see values read-only with an explanation and no save bar", async () => {
    role = "viewer";
    renderWithProviders(<ModelsConfig />);
    expect(
      await screen.findByText(/requires administrator rights/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("llm.agent.base_url")).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Save changes" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Test connection" }),
    ).not.toBeInTheDocument();
  });
});
