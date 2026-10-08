import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Bars, Checks, Heat, PageHeader, Ranked, Records, StatStrip, Tiles, Toggles, Verdict } from "../src/design";

// The one page skeleton every portal tab and console section is built from.
// These pin the parts of the contract a stylesheet cannot: what a verdict is
// allowed to say, how many figures a strip holds, and that a row action is a
// real control.

describe("skeleton", () => {
  it("page header carries eyebrow, title and the actions as real buttons/links", () => {
    render(<PageHeader eyebrow="Watch · your network, right now" title="Overview"
                       actions={[{ label: "Run a check", onClick: () => {} }, { label: "Share", href: "/reports" }]} />);
    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run a check" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Share" })).toHaveAttribute("href", "/reports");
  });

  it("verdict is a status region with a headline in the tone colour and up to three facts", () => {
    render(<Verdict tone="warn" headline="1 thing needs a decision" body="It is waiting on you."
                    facts={[{ value: "64", label: "Devices" }, { value: "1", label: "Waiting", tone: "warn" }, { value: "3", label: "Sites" }, { value: "x", label: "Fourth" }]} />);
    const v = screen.getByRole("status", { name: "Verdict" });
    expect(v.dataset.tone).toBe("warn");
    expect(screen.getByText("1 thing needs a decision")).toBeInTheDocument();
    expect(screen.queryByText("Fourth")).toBeNull();
  });

  it("stat strip holds at most four figures", () => {
    render(<StatStrip items={[1, 2, 3, 4, 5].map((n) => ({ label: `L${n}`, value: String(n) }))} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
  });

  it("checks render a state pill and one sentence per row", () => {
    render(<Checks rows={[{ label: "Firewall", state: "on", tone: "good", sentence: "Refuses unexpected inbound connections." }]} />);
    expect(screen.getByText("on")).toBeInTheDocument();
    expect(screen.getByText(/Refuses unexpected/)).toBeInTheDocument();
  });

  it("ranked bars scale to the largest value", () => {
    const { container } = render(<Ranked rows={[{ name: "a", value: 10 }, { name: "b", value: 5 }]} />);
    const fills = [...container.querySelectorAll(".sk-ranked-track i")].map((i) => (i as HTMLElement).style.width);
    expect(fills).toEqual(["100%", "50%"]);
  });

  it("bars draw one column per datum and never exceed the box", () => {
    const { container } = render(<Bars bars={[{ a: 3 }, { a: 6, b: 6 }, { a: 0 }]} />);
    expect(container.querySelectorAll(".sk-bar")).toHaveLength(3);
    const tallest = container.querySelectorAll(".sk-bar")[1];
    const a = (tallest.querySelector(".sk-bar-a") as HTMLElement).style.height;
    const b = (tallest.querySelector(".sk-bar-b") as HTMLElement).style.height;
    expect(a).toBe("50%"); expect(b).toBe("50%");
  });

  it("heat draws a cell for every row × column and leaves empty cells on the track", () => {
    const { container } = render(<Heat rows={["Mon", "Tue"]} cols={[0, 1, 2]} cells={[{ row: "Mon", col: 1, value: 4 }]} />);
    const cells = container.querySelectorAll(".sk-heat-cell");
    expect(cells).toHaveLength(6);
    expect((cells[1] as HTMLElement).style.background).toContain("color-mix");
    expect((cells[0] as HTMLElement).style.background).toContain("--color-track");
  });

  it("tiles become links or buttons only when given somewhere to go", () => {
    render(<Tiles tiles={[{ title: "Static" }, { title: "Link", href: "/x" }, { title: "Button", onClick: () => {} }]} />);
    expect(screen.getByRole("link", { name: /Link/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Button/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Static/ })).toBeNull();
  });

  it("toggles are real switches with an accessible name", () => {
    render(<Toggles rows={[{ label: "Auto-fix", on: true }]} />);
    expect(screen.getByRole("switch", { name: "Auto-fix" })).toHaveAttribute("aria-checked", "true");
  });

  it("records use mono-caps heads and put a row action in its own cell", () => {
    render(<Records title="Devices" columns={[{ key: "d", header: "Device" }, { key: "k", header: "Kind" }]}
                    rows={[{ id: "1", cells: ["printer", "Printer"], action: { label: "Open →", href: "/assets/devices/aa" } }]} />);
    expect(screen.getByRole("columnheader", { name: "Device" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open →" })).toHaveAttribute("href", "/assets/devices/aa");
  });

  it("an empty record set says so instead of drawing an empty table", () => {
    render(<Records title="Devices" columns={[{ key: "d", header: "Device" }]} rows={[]} empty="No devices yet." />);
    expect(screen.getByText("No devices yet.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("Bars says so in one line rather than drawing 150px of nothing", () => {
    const { container, rerender } = render(
      <Bars bars={[{ a: 0 }, { a: 0, b: 0 }]} empty="Nothing has needed fixing." />,
    );
    expect(screen.getByText("Nothing has needed fixing.")).toBeInTheDocument();
    expect(container.querySelectorAll(".sk-bar")).toHaveLength(0);

    // One non-zero value anywhere is enough to draw the chart instead.
    rerender(<Bars bars={[{ a: 0 }, { a: 0, b: 2 }]} empty="Nothing has needed fixing." />);
    expect(screen.queryByText("Nothing has needed fixing.")).toBeNull();
    expect(container.querySelectorAll(".sk-bar")).toHaveLength(2);
  });
});

