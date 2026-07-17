import { render, screen } from "@testing-library/react";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("renders the status text", () => {
    render(<StatusBadge status="pending" />);
    expect(screen.getByText("pending")).toBeInTheDocument();
  });

  it("styles applied distinctly from rejected", () => {
    const { rerender } = render(<StatusBadge status="applied" />);
    const applied = screen.getByText("applied").className;
    rerender(<StatusBadge status="rejected" />);
    const rejected = screen.getByText("rejected").className;
    expect(applied).not.toEqual(rejected);
  });

  it("tolerates unknown statuses", () => {
    render(<StatusBadge status="something-new" />);
    expect(screen.getByText("something-new")).toBeInTheDocument();
  });
});
