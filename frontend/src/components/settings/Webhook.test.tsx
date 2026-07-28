// Webhook ingress card: both sides of the truth (app secret and the
// paperless workflow), the OUT OF SYNC drift warning, config editing,
// and the one-click setup.

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { WebhookCard } from "./Webhook";
import { renderWithProviders } from "../../test/utils";
import { api, type ConfigRow, type WebhookStatus } from "../../api";

vi.mock("../../api", () => ({
  api: {
    getConfig: vi.fn(),
    putConfig: vi.fn(),
    getWebhookStatus: vi.fn(),
    setupWebhook: vi.fn(),
  },
}));
let role = "admin";
vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ user: "simon", role }),
}));
const mocked = vi.mocked(api);

function status(over: Partial<WebhookStatus> = {}): WebhookStatus {
  return {
    public_url: "https://app.example",
    secret_configured: true,
    workflow_found: true,
    workflow_enabled: true,
    workflow_name: "paperless-llm ingest",
    workflow_synced: true,
    workflow_drift: [],
    workflows_url: "https://paperless.example/workflows",
    ...over,
  };
}

const ROWS: ConfigRow[] = [
  {
    key: "webhook.secret",
    value: null,
    editable: true,
    secret: true,
    source: "default",
    is_set: true,
  },
  {
    key: "webhook.public_url",
    value: "https://app.example",
    editable: true,
    secret: false,
    source: "file",
    is_set: true,
  },
  {
    key: "webhook.redo_ocr",
    value: false,
    editable: true,
    secret: false,
    source: "default",
    is_set: false,
  },
];

describe("Settings — Webhook ingress", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    role = "admin";
    mocked.getConfig.mockResolvedValue(ROWS);
    mocked.getWebhookStatus.mockResolvedValue(status());
  });

  it("healthy: active workflow, its name, and settings in sync", async () => {
    renderWithProviders(<WebhookCard />);
    expect(await screen.findByText("workflow active")).toBeInTheDocument();
    expect(screen.getByText("secret configured")).toBeInTheDocument();
    expect(screen.getByText("paperless-llm ingest")).toBeInTheDocument();
    expect(screen.getByText(/settings in sync/)).toBeInTheDocument();
    const manage = screen.getByRole("link", { name: /paperless workflows/ });
    expect(manage.getAttribute("href")).toBe("https://paperless.example/workflows");
  });

  it("missing secret reads as disabled ingress", async () => {
    mocked.getWebhookStatus.mockResolvedValue(status({ secret_configured: false }));
    renderWithProviders(<WebhookCard />);
    expect(
      await screen.findByText("no secret — ingress disabled"),
    ).toBeInTheDocument();
  });

  it("drifted workflow shouts OUT OF SYNC and names what drifted", async () => {
    mocked.getWebhookStatus.mockResolvedValue(
      status({ workflow_synced: false, workflow_drift: ["url", "secret"] }),
    );
    renderWithProviders(<WebhookCard />);
    expect(
      await screen.findByText(/workflow OUT OF SYNC \(url, secret\)/),
    ).toBeInTheDocument();
    expect(screen.getByText(/re-run “Set up automatically” to heal it/)).toBeInTheDocument();
    // Drift replaces the healthy line entirely.
    expect(screen.queryByText("workflow active")).not.toBeInTheDocument();
  });

  it("no workflow at all is called out as the missing half", async () => {
    mocked.getWebhookStatus.mockResolvedValue(
      status({ workflow_found: false, workflow_synced: null }),
    );
    renderWithProviders(<WebhookCard />);
    expect(
      await screen.findByText("no paperless workflow posts to this app"),
    ).toBeInTheDocument();
  });

  it("a paperless without a workflows API reads unknown, not broken", async () => {
    mocked.getWebhookStatus.mockResolvedValue(
      status({ workflow_found: null, workflow_synced: null }),
    );
    renderWithProviders(<WebhookCard />);
    expect(
      await screen.findByText(/unknown — this paperless exposes no workflows API/),
    ).toBeInTheDocument();
  });

  it("a found but disabled workflow says DISABLED", async () => {
    mocked.getWebhookStatus.mockResolvedValue(status({ workflow_enabled: false }));
    renderWithProviders(<WebhookCard />);
    expect(await screen.findByText("workflow DISABLED")).toBeInTheDocument();
  });

  it("edits save via the config API; setup is blocked while dirty", async () => {
    mocked.putConfig.mockResolvedValue(ROWS);
    renderWithProviders(<WebhookCard />);
    const url = await screen.findByLabelText("webhook.public_url");
    // Save/Discard only appear once something is dirty.
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
    await userEvent.clear(url);
    await userEvent.type(url, "https://new.example");
    // Dirty: the one-click setup steps back until the edit is settled.
    expect(screen.getByRole("button", { name: "Set up automatically" })).toBeDisabled();
    expect(screen.getByText("save your changes first")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(mocked.putConfig).toHaveBeenCalledWith({
        "webhook.public_url": "https://new.example",
      }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Set up automatically" }),
      ).toBeEnabled(),
    );
  });

  it("Set up automatically reports the outcome incl. a generated secret", async () => {
    mocked.setupWebhook.mockResolvedValue({
      ok: true,
      created: true,
      message: "Workflow created",
      secret_generated: true,
      workflow_id: 5,
      workflow_name: "paperless-llm ingest",
    });
    renderWithProviders(<WebhookCard />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Set up automatically" }),
    );
    await waitFor(() => expect(mocked.setupWebhook).toHaveBeenCalled());
    expect(await screen.findByText(/✓ Workflow created/)).toBeInTheDocument();
    expect(screen.getByText(/a new secret was generated/)).toBeInTheDocument();
  });

  it("a failed setup shows the failure message", async () => {
    mocked.setupWebhook.mockResolvedValue({
      ok: false,
      created: false,
      message: "public URL is not set",
      secret_generated: false,
      workflow_id: null,
      workflow_name: "",
    });
    renderWithProviders(<WebhookCard />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Set up automatically" }),
    );
    expect(await screen.findByText(/✗ public URL is not set/)).toBeInTheDocument();
  });

  it("non-admins get status only: no setup button, locked fields", async () => {
    role = "viewer";
    renderWithProviders(<WebhookCard />);
    expect(await screen.findByText("workflow active")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Set up automatically" }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("webhook.public_url")).toBeDisabled();
    // The manage link is an admin affordance.
    expect(
      screen.queryByRole("link", { name: /paperless workflows/ }),
    ).not.toBeInTheDocument();
  });
});
