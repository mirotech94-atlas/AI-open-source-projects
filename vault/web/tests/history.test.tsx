// Task 57: the History tab — trend panels, the scan log and the event log.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MeOut } from "../src/api";
import { ToastProvider } from "../src/design";
import type { HistoryPayload } from "../src/types";
import History from "../src/pages/History";

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return { ...actual, me: vi.fn() };
});

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, historyPayload: vi.fn() };
});

import { me } from "../src/api";
import { historyPayload } from "../src/lib/api";

const fixtureMe: MeOut = {
  user: { username: "miro", display_name: "Miro", is_superadmin: false },
  tenants: [{ id: "t1", slug: "home", name: "HomeWiFi", role: "viewer" }],
  active_tenant: { id: "t1", slug: "home", name: "HomeWiFi", role: "viewer" },
  chat_providers: [],
};

function points(values: number[]) {
  return values.map((value, i) => ({ ts: `2026-09-0${i + 1}T00:00:00+00:00`, value }));
}

const fixture: HistoryPayload = {
  days: 7,
  hours: 0,
  network: null,
  scan_count: 412,
  first_ts: "2026-09-01T00:00:00+00:00",
  last_ts: "2026-09-08T12:00:00+00:00",
  charts: [
    {
      key: "latency",
      label: "Latency",
      unit: "ms",
      max: null,
      series: [
        { name: "status page", points: points([120, 140, 118]), color: null },
        { name: "docs", points: points([90, 95, 92]), color: null },
        { name: "shop", points: points([210, 190, 205]), color: null },
      ],
    },
    {
      key: "disk",
      label: "Disk",
      unit: "%",
      max: 100,
      series: [{ name: "labnet-1", points: points([54, 56, 57]), color: null }],
    },
  ],
  scans: [
    {
      ts: "2026-09-08T12:00:00+00:00",
      sites_ok: 3,
      sites_total: 4,
      hosts_ok: 2,
      hosts_total: 2,
      devices: 15,
      crit: 1,
      warn: 2,
      score: 85,
    },
    {
      ts: "2026-09-08T11:00:00+00:00",
      sites_ok: 4,
      sites_total: 4,
      hosts_ok: 2,
      hosts_total: 2,
      devices: 14,
      crit: 0,
      warn: 0,
      score: 88,
    },
  ],
  events: [
    {
      ts: "2026-09-08T11:40:00+00:00",
      kind: "device joined",
      entity: "aa:bb:cc:dd:ee:ff",
      detail: "LabNet",
      href: "/assets/devices/aa:bb:cc:dd:ee:ff",
      network: { id: "n2", slug: "labnet", name: "LabNet", colour: "" },
    },
  ],
};

const EMPTY: HistoryPayload = {
  days: 7,
  hours: 0,
  network: null,
  scan_count: 0,
  first_ts: null,
  last_ts: null,
  charts: [],
  scans: [],
  events: [],
};

function renderHistory(payload: HistoryPayload = fixture) {
  vi.mocked(me).mockResolvedValue(fixtureMe);
  vi.mocked(historyPayload).mockResolvedValue(payload);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/history"]}>
          <History />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("History", () => {
  it("reports the range's scan count and draws a panel per chart", async () => {
    renderHistory();
    expect(await screen.findByText(/412 scans in range/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Latency" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Disk" })).toBeInTheDocument();
  });

  it("offers a series picker only where a panel has more series than a chart can draw", async () => {
    renderHistory();
    await screen.findByRole("heading", { name: "Latency" });
    expect(screen.getByLabelText("Series shown in Latency")).toBeInTheDocument();
    expect(screen.queryByLabelText("Series shown in Disk")).not.toBeInTheDocument();

    // The third series is selectable even though only two are drawn at once.
    const picker = screen.getByLabelText("Series shown in Latency");
    await userEvent.click(within(picker).getByRole("button", { name: /shop/i }));
    expect(within(picker).getByRole("button", { name: /shop/i })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows the scan log newest first with hub-side counts", async () => {
    renderHistory();
    await screen.findByRole("heading", { name: "Scan log" });
    const table = screen.getByRole("heading", { name: "Scan log" }).closest("section")!;
    for (const header of ["Time", "Sites", "Hosts", "Devices", "Score", "Alerts"]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    expect(within(table).getByText("1 crit")).toBeInTheDocument();
    expect(within(table).getByText("2 warn")).toBeInTheDocument();
  });

  it("links an event row at the hub's own href and names its network", async () => {
    renderHistory();
    await screen.findByRole("heading", { name: "Event log" });
    expect(screen.getByRole("link", { name: "aa:bb:cc:dd:ee:ff" })).toHaveAttribute(
      "href",
      "/assets/devices/aa:bb:cc:dd:ee:ff",
    );
    expect(screen.getByTestId("network-badge")).toHaveTextContent("LabNet");
  });

  it("changing the range refetches with the new day count", async () => {
    renderHistory();
    await screen.findByRole("heading", { name: "Latency" });
    await userEvent.click(screen.getByRole("tab", { name: "30d" }));
    expect(historyPayload).toHaveBeenCalledWith(30, null, 0);
  });

  it("offers the reference's 6-hour range and asks the hub for it in hours", async () => {
    renderHistory();
    await screen.findByRole("heading", { name: "Latency" });
    expect(screen.getByRole("tab", { name: "6h" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "6h" }));
    // `days` cannot go below 1, so a sub-day window travels as `hours`.
    expect(historyPayload).toHaveBeenCalledWith(1, null, 6);
  });

  // History has two distinct empty states now: a tenant with no history at
  // all ("just started") vs. a real range that simply has nothing in it
  // ("widen the range") — the reader should never have to guess which one
  // they are looking at from a blank page alone (`first_ts` is the hub's
  // signal for "has a scan ever completed", not just "any in this window").
  it("says Vault has just started when there is no history at all", async () => {
    renderHistory(EMPTY);
    expect(await screen.findByText(/vault has just started/i)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Scan log" })).not.toBeInTheDocument();
  });

  it("says to widen the range when history exists but this window is empty", async () => {
    renderHistory({ ...EMPTY, first_ts: "2026-09-01T00:00:00+00:00" });
    expect(await screen.findByText(/nothing changed in this window/i)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Scan log" })).not.toBeInTheDocument();
  });
});
