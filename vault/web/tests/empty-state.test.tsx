import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { EmptyState } from "../src/components/EmptyState";

describe("EmptyState", () => {
  it("renders an href action as a router Link inside a Router, keeping the client-side navigation", () => {
    render(
      <MemoryRouter>
        <EmptyState title="Nothing yet" action={{ label: "Go to Settings", href: "/settings/sensors" }} />
      </MemoryRouter>,
    );

    const link = screen.getByRole("link", { name: "Go to Settings" });
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "/settings/sensors");
  });

  it("falls back to a plain anchor outside a Router, so router-less unit tests of its callers keep passing", () => {
    render(<EmptyState title="Nothing yet" action={{ label: "Go to Settings", href: "/settings/sensors" }} />);

    const link = screen.getByRole("link", { name: "Go to Settings" });
    expect(link).toHaveAttribute("href", "/settings/sensors");
  });

  it("still renders an onClick action as a plain button either way", () => {
    render(<EmptyState title="Nothing yet" action={{ label: "Retry", onClick: () => {} }} />);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
