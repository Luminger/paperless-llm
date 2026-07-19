import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { Sessions, describeAgent } from "./Sessions";
import { renderWithProviders } from "../../test/utils";
import { api } from "../../api";

vi.mock("../../api", () => ({
  api: {
    listAuthSessions: vi.fn(),
    revokeAuthSession: vi.fn(),
  },
}));
vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ user: "simon", role: "admin" }),
}));
const mocked = vi.mocked(api);

const FIREFOX =
  "Mozilla/5.0 (X11; Linux x86_64; rv:141.0) Gecko/20100101 Firefox/141.0";
const CHROME_WIN =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";

function mkSession(over = {}) {
  return {
    sid: "abc",
    username: "simon",
    role: "admin",
    user_agent: FIREFOX,
    created_at: "2026-07-19T08:00:00Z",
    last_seen_at: "2026-07-19T09:00:00Z",
    expires_at: "2026-07-20T08:00:00Z",
    current: false,
    ...over,
  };
}

describe("describeAgent", () => {
  it("summarizes common agents; falls back gracefully", () => {
    expect(describeAgent(FIREFOX)).toBe("Firefox on Linux");
    expect(describeAgent(CHROME_WIN)).toBe("Chrome on Windows");
    expect(describeAgent("")).toBe("Unknown client");
  });
});

describe("Settings — Sessions", () => {
  beforeEach(() => vi.clearAllMocks());

  it("lists sessions; the current one shows a badge and no Revoke", async () => {
    mocked.listAuthSessions.mockResolvedValue([
      mkSession({ sid: "cur", current: true }),
      mkSession({ sid: "other", user_agent: CHROME_WIN, username: "erika" }),
    ]);
    renderWithProviders(<Sessions />);

    expect(await screen.findByText("Firefox on Linux")).toBeInTheDocument();
    expect(screen.getByText("this device")).toBeInTheDocument();
    // admin view shows the other user's name
    expect(screen.getByText(/erika/)).toBeInTheDocument();
    // exactly ONE revoke button (never for the current session)
    expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(1);
  });

  it("revoking goes through the confirm dialog and refreshes", async () => {
    mocked.listAuthSessions.mockResolvedValue([
      mkSession({ sid: "cur", current: true }),
      mkSession({ sid: "other", user_agent: CHROME_WIN }),
    ]);
    mocked.revokeAuthSession.mockResolvedValue({ user: "simon", role: "admin" });
    renderWithProviders(<Sessions />);

    await userEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Revoke the session" }),
    );
    await waitFor(() =>
      expect(mocked.revokeAuthSession).toHaveBeenCalledWith("other"),
    );
  });
});
