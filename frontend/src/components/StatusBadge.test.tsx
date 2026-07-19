import { render as rtlRender, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { TooltipProvider } from "@/components/ui/tooltip";
import userEvent from "@testing-library/user-event";
import { SessionStatusBadge, StatusBadge } from "./StatusBadge";

// Some statuses carry a hover explanation (UI-U4) — mirror the app's
// tooltip provider.
const render = (ui: ReactElement) =>
  rtlRender(<TooltipProvider>{ui}</TooltipProvider>);

describe("StatusBadge", () => {
  it("renders the status text", () => {
    render(<StatusBadge status="pending" />);
    expect(screen.getByText("pending")).toBeInTheDocument();
  });

  it("styles applied distinctly from rejected", () => {
    const { rerender } = render(<StatusBadge status="applied" />);
    const applied = screen.getByText("applied").className;
    rerender(
      <TooltipProvider>
        <StatusBadge status="rejected" />
      </TooltipProvider>,
    );
    const rejected = screen.getByText("rejected").className;
    expect(applied).not.toEqual(rejected);
  });

  it("tolerates unknown statuses", () => {
    render(<StatusBadge status="something-new" />);
    expect(screen.getByText("something-new")).toBeInTheDocument();
  });

  it("failed session badge reveals the error as a tooltip (UI-U4)", async () => {
    render(
      <SessionStatusBadge status="failed" phase="done" error="LLM endpoint unreachable" />,
    );
    const badge = screen.getByText("Error");
    await userEvent.hover(badge);
    expect(
      (await screen.findAllByText(/LLM endpoint unreachable/)).length,
    ).toBeGreaterThan(0);
  });

  it("superseded explains itself on hover", async () => {
    render(<StatusBadge status="superseded" />);
    await userEvent.hover(screen.getByText("superseded"));
    expect(
      (await screen.findAllByText(/newer revision/)).length,
    ).toBeGreaterThan(0);
  });
});
