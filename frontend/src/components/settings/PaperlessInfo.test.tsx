// The read-only Paperless tab: connection facts, the TLS warning, and
// the deliberate absence of any way to edit the connection at runtime.

import { screen } from "@testing-library/react";
import { vi } from "vitest";
import { PaperlessInfo } from "./PaperlessInfo";
import { renderWithProviders } from "../../test/utils";
import { api, type SettingsOverview } from "../../api";

vi.mock("../../api", () => ({
  api: {
    getSettingsOverview: vi.fn(),
    // The embedded WebhookCard's queries:
    getConfig: vi.fn(),
    putConfig: vi.fn(),
    getWebhookStatus: vi.fn(),
    setupWebhook: vi.fn(),
  },
}));
vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ user: "simon", role: "admin" }),
}));
const mocked = vi.mocked(api);

function overview(paperless: Partial<SettingsOverview["paperless"]> = {}) {
  return {
    paperless: {
      auth: "token (paperless superuser)",
      base_url: "http://paperless:8000/api",
      external_url: "https://paperless.example",
      timeout_seconds: 30,
      verify_tls: true,
      ...paperless,
    },
  } as SettingsOverview;
}

describe("Settings — Paperless info", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getConfig.mockResolvedValue([]);
    mocked.getWebhookStatus.mockResolvedValue({
      public_url: "",
      secret_configured: false,
      workflow_found: null,
      workflow_enabled: true,
      workflow_name: "",
      workflow_synced: null,
      workflow_drift: [],
      workflows_url: "https://paperless.example/workflows",
    });
  });

  it("shows the instance link, endpoint, credentials and timeout", async () => {
    mocked.getSettingsOverview.mockResolvedValue(overview());
    renderWithProviders(<PaperlessInfo />);

    const link = await screen.findByRole("link", {
      name: "https://paperless.example",
    });
    expect(link.getAttribute("href")).toBe("https://paperless.example");
    expect(screen.getByText("http://paperless:8000/api")).toBeInTheDocument();
    expect(screen.getByText("token (paperless superuser)")).toBeInTheDocument();
    expect(screen.getByText("30s")).toBeInTheDocument();
    // The connection is read-only by design — the page says so.
    expect(
      screen.getByText(/never at runtime, so a bad value can't lock you/),
    ).toBeInTheDocument();
  });

  it("verified TLS stays quiet", async () => {
    mocked.getSettingsOverview.mockResolvedValue(overview());
    renderWithProviders(<PaperlessInfo />);
    expect(await screen.findByText("verified")).toBeInTheDocument();
    expect(screen.queryByText(/certificate & host checks are off/)).toBeNull();
  });

  it("disabled TLS verification carries a loud warning", async () => {
    mocked.getSettingsOverview.mockResolvedValue(overview({ verify_tls: false }));
    renderWithProviders(<PaperlessInfo />);
    expect(await screen.findByText("DISABLED")).toBeInTheDocument();
    expect(
      screen.getByText(/certificate & host checks are off — self-signed setups only/),
    ).toBeInTheDocument();
  });

  it("surfaces a load failure as the error notice", async () => {
    mocked.getSettingsOverview.mockRejectedValue(new Error("paperless unreachable"));
    renderWithProviders(<PaperlessInfo />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "paperless unreachable",
    );
  });
});
