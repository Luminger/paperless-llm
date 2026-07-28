// The front door: credentials go to paperless, failures come back as
// readable text, and half-filled forms cannot be submitted.

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import Login from "./Login";
import { renderWithProviders } from "../test/utils";
import { api } from "../api";
import { ApiError } from "../lib/errors";

vi.mock("../api", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api")>();
  return { ...orig, api: { login: vi.fn() } };
});
const mocked = vi.mocked(api);

describe("Login", () => {
  beforeEach(() => vi.clearAllMocks());

  it("submits the typed credentials", async () => {
    mocked.login.mockResolvedValue({ user: "simon", role: "admin" } as never);
    renderWithProviders(<Login />);
    await userEvent.type(screen.getByLabelText("Username"), "simon");
    await userEvent.type(screen.getByLabelText("Password"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(mocked.login).toHaveBeenCalledWith("simon", "hunter2"));
  });

  it("stays disabled until both fields are filled", async () => {
    renderWithProviders(<Login />);
    const submit = screen.getByRole("button", { name: "Sign in" });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Username"), "simon");
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Password"), "x");
    expect(submit).toBeEnabled();
  });

  it("shows the backend's message on rejected credentials", async () => {
    mocked.login.mockRejectedValue(
      new ApiError(401, "invalid_credentials", "Wrong username or password."),
    );
    renderWithProviders(<Login />);
    await userEvent.type(screen.getByLabelText("Username"), "simon");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(
      await screen.findByText("Wrong username or password."),
    ).toBeInTheDocument();
  });
});
