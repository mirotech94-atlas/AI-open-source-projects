import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  Badge,
  Card,
  Chart,
  Chips,
  Drawer,
  Heatmap,
  Ring,
  Sparkline,
  Stat,
  Table,
  Tabs,
  Timeline,
  ToastProvider,
  useToast,
} from "../src/design";

describe("design system", () => {
  it("Table sorts locally and renders an empty state", async () => {
    render(<Table columns={[{ key: "n", header: "N" }]} rows={[]} empty="nothing here" rowKey={(r) => r.n} />);
    expect(screen.getByText("nothing here")).toBeInTheDocument();
  });
  it("Drawer closes on Escape and restores focus", async () => {
    const onClose = vi.fn();
    render(<Drawer open title="Device" onClose={onClose}><p>body</p></Drawer>);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });
  it("Chips report the selected group", async () => {
    const onChange = vi.fn();
    render(<Chips items={[{ value: "Cameras", label: "Cameras", count: 2 }]} value={[]} multiple onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /Cameras/ }));
    expect(onChange).toHaveBeenCalledWith(["Cameras"]);
  });
  it("Ring exposes its value to assistive tech", () => {
    render(<Ring value={83} max={100} grade="B" label="Security score" />);
    expect(screen.getByRole("meter", { name: /Security score/ })).toHaveAttribute("aria-valuenow", "83");
  });
});

// --- coverage for the rest of the vocabulary every later web task builds on ---

describe("Table", () => {
  const rows = [
    { name: "cr1000a", risk: 15 },
    { name: "atv", risk: 60 },
    { name: "cam-1", risk: null },
  ];
  const columns = [
    { key: "name", header: "Name" },
    { key: "risk", header: "Risk", align: "right" as const },
  ];

  it("sorts the given rows locally, nulls last, without re-fetching", async () => {
    const onSort = vi.fn();
    const { rerender } = render(
      <Table
        columns={columns}
        rows={rows}
        rowKey={(r) => r.name}
        sort={{ key: "risk", dir: "asc" }}
        onSort={onSort}
      />,
    );
    const order = () =>
      screen.getAllByRole("row").slice(1).map((tr) => tr.firstElementChild?.textContent);
    expect(order()).toEqual(["cr1000a", "atv", "cam-1"]);

    rerender(
      <Table
        columns={columns}
        rows={rows}
        rowKey={(r) => r.name}
        sort={{ key: "risk", dir: "desc" }}
        onSort={onSort}
      />,
    );
    expect(order()).toEqual(["atv", "cr1000a", "cam-1"]);

    await userEvent.click(screen.getByRole("button", { name: /Name/ }));
    expect(onSort).toHaveBeenCalledWith({ key: "name", dir: "asc" });
  });

  it("exposes the sorted column through aria-sort", () => {
    render(
      <Table
        columns={columns}
        rows={rows}
        rowKey={(r) => r.name}
        sort={{ key: "risk", dir: "desc" }}
        onSort={vi.fn()}
      />,
    );
    expect(screen.getByRole("columnheader", { name: /Risk/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
  });

  it("activates a row from the keyboard", async () => {
    const onRowClick = vi.fn();
    render(
      <Table columns={columns} rows={rows} rowKey={(r) => r.name} onRowClick={onRowClick} />,
    );
    const first = screen.getAllByRole("row")[1];
    first.focus();
    await userEvent.keyboard("{Enter}");
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);
  });
});

describe("Drawer", () => {
  it("closes on a backdrop click but not on a click inside the panel", async () => {
    const onClose = vi.fn();
    render(
      <Drawer open title="Device" onClose={onClose}>
        <button type="button">Quarantine</button>
      </Drawer>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Quarantine" }));
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(document.querySelector(".g-drawer-backdrop") as HTMLElement);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("is a labelled modal dialog and renders nothing when closed", () => {
    const { rerender } = render(
      <Drawer open title="Device" subtitle="192.168.1.1" onClose={vi.fn()}>
        <p>body</p>
      </Drawer>,
    );
    const dialog = screen.getByRole("dialog", { name: "Device" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    rerender(
      <Drawer open={false} title="Device" onClose={vi.fn()}>
        <p>body</p>
      </Drawer>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("Tabs and Chips", () => {
  const items = [
    { value: "sites", label: "Websites" },
    { value: "hosts", label: "Servers", count: 3 },
  ];

  it("moves between tabs with the arrow keys", async () => {
    const onChange = vi.fn();
    render(<Tabs items={items} value="sites" onChange={onChange} label="Assets" />);
    screen.getByRole("tab", { name: /Websites/ }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(onChange).toHaveBeenCalledWith("hosts");
    expect(screen.getByRole("tab", { name: /Websites/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("deselects a multi-select chip that is already on", async () => {
    const onChange = vi.fn();
    render(
      <Chips
        items={[{ value: "Cameras", label: "Cameras" }]}
        value={["Cameras"]}
        multiple
        onChange={onChange}
      />,
    );
    const chip = screen.getByRole("button", { name: /Cameras/ });
    expect(chip).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(chip);
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("clears a single-select chip on a second click", async () => {
    const onChange = vi.fn();
    render(
      <Chips items={[{ value: "crit", label: "Critical" }]} value="crit" onChange={onChange} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Critical/ }));
    expect(onChange).toHaveBeenCalledWith(null);
  });
});

describe("Card, Stat and Badge", () => {
  it("puts tone on the card without recolouring the surface", () => {
    render(
      <Card title="Wi-Fi guard" subtitle="one verdict" tone="alert" actions={<span>fix</span>}>
        <p>body</p>
      </Card>,
    );
    const card = screen.getByRole("heading", { name: "Wi-Fi guard" }).closest("section");
    expect(card?.className).toContain("g-tone-alert");
    expect(screen.getByText("one verdict")).toBeInTheDocument();
    expect(screen.getByText("fix")).toBeInTheDocument();
  });

  it("renders a mono stat with tabular numerals", () => {
    render(<Stat label="Latency" value="182" unit="ms" sub="p95" mono tone="warn" />);
    expect(screen.getByText("182").className).toContain("g-stat-mono");
    expect(screen.getByText("ms")).toBeInTheDocument();
    expect(screen.getByText("p95")).toBeInTheDocument();
  });

  it("renders a toned badge as plain text, not a control", () => {
    render(<Badge tone="good">Online</Badge>);
    const badge = screen.getByText("Online");
    expect(badge.className).toContain("g-tone-good");
    expect(badge).not.toHaveAttribute("tabindex");
  });
});

describe("Sparkline, Ring and Chart", () => {
  it("draws a path from the points and skips a single reading", () => {
    const { container, rerender } = render(<Sparkline points={[10, 40, 20]} />);
    expect(container.querySelector("path.g-spark-path")?.getAttribute("d")).toMatch(/^M0/);
    rerender(<Sparkline points={[10]} />);
    expect(container.querySelector("path.g-spark-path")).toBeNull();
  });

  it("reports a missing score as plain rather than as an alert", () => {
    render(<Ring value={null} label="Security score" />);
    const meter = screen.getByRole("meter", { name: /Security score/ });
    expect(meter).not.toHaveAttribute("aria-valuenow");
    expect(meter).toHaveAttribute("aria-valuetext", "no score yet");
  });

  it("draws at most two series, in the two permitted blues", () => {
    const series = ["score", "potential", "third"].map((name) => ({
      name,
      color: null,
      points: [
        { ts: "2026-09-01T00:00:00Z", value: 70 },
        { ts: "2026-09-02T00:00:00Z", value: 83 },
      ],
    }));
    const { container } = render(<Chart series={series} yLabel="Score" />);
    const strokes = Array.from(container.querySelectorAll("path.g-chart-line")).map((p) =>
      p.getAttribute("stroke"),
    );
    expect(strokes).toEqual(["var(--color-primary)", "var(--color-accent-2)"]);
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("score, potential");
  });

  it("says so when it has no points at all", () => {
    render(<Chart series={[{ name: "score", color: null, points: [] }]} />);
    expect(screen.getByText(/No data for this range yet/)).toBeInTheDocument();
  });
});

describe("Heatmap and Timeline", () => {
  it("renders the hub's normalised cells and reports the one hovered", async () => {
    const onCell = vi.fn();
    render(
      <Heatmap
        cells={[{ row: "mac-a", col: 0, value: 80 }]}
        rows={["mac-a"]}
        cols={[0, 1]}
        onCell={onCell}
        format={(v) => `${v}%`}
      />,
    );
    const cell = screen.getByRole("button", { name: "mac-a, 0: 80%" });
    await userEvent.click(cell);
    expect(onCell).toHaveBeenCalledWith({ row: "mac-a", col: 0, value: 80 });
    expect(screen.getByRole("status").textContent).toBe("mac-a · 0 · 80%");
  });

  it("stripes a timeline row by severity and shows its age", async () => {
    const onSelect = vi.fn();
    const item = {
      ts: new Date(Date.now() - 7 * 60_000).toISOString(),
      kind: "alert",
      title: "New device joined",
      detail: "58:96:71:61:05:e3",
      sev: "CRIT" as const,
      entity: "home",
      href: "/assets/devices/58:96:71:61:05:e3",
    };
    render(<Timeline items={[item]} onSelect={onSelect} />);
    expect(screen.getByText("7m ago")).toBeInTheDocument();
    expect(document.querySelector(".g-timeline-stripe")?.className).toContain("g-tone-alert");
    await userEvent.click(screen.getByRole("button", { name: /New device joined/ }));
    expect(onSelect).toHaveBeenCalledWith(item);
  });

  it("renders an empty timeline as a sentence, not a blank box", () => {
    render(<Timeline items={[]} />);
    expect(screen.getByText(/Nothing has changed yet/)).toBeInTheDocument();
  });
});

describe("Toast", () => {
  function Trigger() {
    const { show } = useToast();
    return (
      <button type="button" onClick={() => show("Fix queued", "good")}>
        go
      </button>
    );
  }

  it("announces a toast and auto-dismisses it after six seconds", async () => {
    vi.useFakeTimers();
    try {
      render(
        <ToastProvider>
          <Trigger />
        </ToastProvider>,
      );
      // `fireEvent`, not `userEvent`: userEvent's own timer plumbing deadlocks
      // against Vitest's fake clock here, and the assertion is about the
      // provider's 6 s timeout, not about pointer behaviour.
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "go" }));
      });
      expect(screen.getByRole("status")).toHaveTextContent("Fix queued");
      await act(async () => {
        vi.advanceTimersByTime(6000);
      });
      expect(screen.queryByRole("status")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("refuses to be used outside its provider", () => {
    const onError = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Trigger />)).toThrow(/ToastProvider/);
    onError.mockRestore();
  });
});
