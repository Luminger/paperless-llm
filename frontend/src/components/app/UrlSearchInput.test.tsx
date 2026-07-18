// AUDIT FP-H2: the URL is the source of truth — an external URL change
// (nav-click to the bare route) must win over the local edit buffer,
// never be resurrected by it.
import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate, useSearchParams } from "react-router-dom";
import { UrlSearchInput } from "./UrlSearchInput";

function Harness() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  return (
    <div>
      <UrlSearchInput ariaLabel="search" />
      <span data-testid="url-q">{params.get("q") ?? ""}</span>
      <button onClick={() => navigate("/list")}>nav-clear</button>
    </div>
  );
}

describe("UrlSearchInput", () => {
  it("debounces typing into the URL", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/list"]}>
        <Harness />
      </MemoryRouter>,
    );
    await user.type(screen.getByLabelText("search"), "invoice");
    await waitFor(() =>
      expect(screen.getByTestId("url-q").textContent).toBe("invoice"),
    );
  });

  it("external URL clear wins over the edit buffer (no resurrection)", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/list?q=invoice"]}>
        <Harness />
      </MemoryRouter>,
    );
    const input = screen.getByLabelText("search") as HTMLInputElement;
    expect(input.value).toBe("invoice");

    await user.click(screen.getByText("nav-clear"));
    // Input follows the URL immediately…
    await waitFor(() => expect(input.value).toBe(""));
    // …and the old query must NOT come back after the debounce window.
    await new Promise((r) => setTimeout(r, 500));
    expect(screen.getByTestId("url-q").textContent).toBe("");
    expect(input.value).toBe("");
  });

  it("a query of literally '0' is expressible", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/list"]}>
        <Harness />
      </MemoryRouter>,
    );
    await user.type(screen.getByLabelText("search"), "0");
    await waitFor(() =>
      expect(screen.getByTestId("url-q").textContent).toBe("0"),
    );
  });
});
