/** Task 70: the nine summary tiles the Devices view opens with. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import { DeviceSummaryTiles } from "../src/components/DeviceSummaryTiles";
import Assets from "../src/pages/Assets";
import type { DeviceTiles, DevicesPayload, NetworkHealth } from "../src/types";
import { NO_TILES } from "./tiles";

vi.mock("../src/lib/live", () => ({
  useLiveFeed: () => ({ status: "open" as const, last: null, on: () => () => {} }),
}));

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    sitesPayload: vi.fn().mockResolvedValue({ sites: [] }),
    hostsPayload: vi.fn().mockResolvedValue({ hosts: [] }),
    devicesPayload: vi.fn(),
    deviceDetail: vi.fn(),
    screenTimePayload: vi.fn().mockResolvedValue({ days: 0, scans: 0, devices: [], heatmap: [] }),
    settingsPayload: vi.fn(),
    me: vi.fn().mockResolvedValue(null),
  };
});

import { devicesPayload } from "../src/lib/api";

const tiles = (over: Partial<DeviceTiles> = {}): DeviceTiles => ({ ...NO_TILES, ...over });

function payload(over: Partial<DevicesPayload> = {}): DevicesPayload {
  return {
    groups: [], chips: [], total: 25, online: 19, unverified: 0,
    cameras: 0, cameras_online: 0, tiles: NO_TILES, networks: [], charts: [], ...over,
  };
}

function renderTiles(over: Partial<DevicesPayload> = {}) {
  return render(<DeviceSummaryTiles payload={payload(over)} />);
}

/** The MOST RECENT tile row on screen: several tests render the component more
 *  than once in one `it`, and testing-library only cleans up between tests. */
function labelled(label: string): HTMLElement {
  const rows = screen.getAllByTestId("device-tiles");
  const node = [...rows[rows.length - 1].children].find(
    (card) => card.querySelector(".g-stat-label")?.textContent === label,
  );
  if (!node) throw new Error(`no tile labelled ${label}`);
  return node as HTMLElement;
}

function valueOf(label: string): string {
  return labelled(label).querySelector(".g-stat-value")?.textContent ?? "";
}

function unitOf(label: string): string {
  return labelled(label).querySelector(".g-stat-unit")?.textContent ?? "";
}

describe("Devices summary tiles", () => {
  it("draws the reference's nine tiles, in its order", () => {
    renderTiles();
    const labels = [...screen.getByTestId("device-tiles").children].map(
      (card) => card.querySelector(".g-stat-label")?.textContent,
    );
    expect(labels).toEqual([
      "Devices online", "New this scan", "Trusted", "Randomized MACs",
      "Download", "Upload", "Latency", "DNS (system)", "WAN IP",
    ]);
  });

  it("puts the unit and the denominator in the smaller weight beside the number", () => {
    renderTiles({
      tiles: tiles({ down_mbps: 526.6, up_mbps: 86.3, latency_ms: 21, dns_ms: 96, dns_ok: true }),
    });
    expect(valueOf("Devices online")).toBe("19");
    expect(unitOf("Devices online")).toBe("/ 25 known");
    expect(valueOf("Download")).toBe("526.6");
    expect(unitOf("Download")).toBe("Mbps");
    expect(valueOf("Latency")).toBe("21");
    expect(unitOf("Latency")).toBe("ms");
    expect(valueOf("DNS (system)")).toBe("96");
    expect(unitOf("DNS (system)")).toBe("ms");
  });

  it("shows a dash, never a zero, for a figure nobody measured", () => {
    renderTiles();
    for (const label of ["Download", "Upload", "Latency", "DNS (system)", "WAN IP"]) {
      expect(valueOf(label)).toBe("—");
    }
    // A count of zero IS the measurement, so it prints as a number.
    expect(valueOf("Trusted")).toBe("0");
    expect(valueOf("Randomized MACs")).toBe("0");
  });

  it("tones New this scan on its own value and nothing else", () => {
    renderTiles();
    expect(labelled("New this scan").className).toContain("g-tone-good");
    renderTiles({ tiles: tiles({ new_devices: 3 }) });
    expect(labelled("New this scan").className).toContain("g-tone-warn");
  });

  it("makes DNS crit when the resolver failed, not when it was never sampled", () => {
    renderTiles({ tiles: tiles({ dns_ok: false, dns_ms: 2000 }) });
    expect(valueOf("DNS (system)")).toBe("FAIL");
    expect(labelled("DNS (system)").className).toContain("g-tone-alert");
    renderTiles();
    expect(labelled("DNS (system)").className).toContain("g-tone-plain");
  });

  it("names the WAN address when there is one, and counts them when there are two", () => {
    renderTiles({ tiles: tiles({ wan_ip: "203.0.113.4", wan_ips: ["203.0.113.4"] }) });
    expect(valueOf("WAN IP")).toBe("203.0.113.4");

    renderTiles({ tiles: tiles({ wan_ip: "", wan_ips: ["203.0.113.4", "198.51.100.7"] }) });
    expect(valueOf("WAN IP")).toBe("2");
    expect(unitOf("WAN IP")).toBe("addresses");
    expect(labelled("WAN IP").title).toBe("203.0.113.4, 198.51.100.7");
  });
});

// --- the tiles follow the network chooser ------------------------------------

const health = (over: Partial<NetworkHealth>): NetworkHealth => ({
  id: "net_a", slug: "a", name: "Office Wi-Fi", colour: "", state: "good",
  status: "active", devices: 20, online: 16, unverified: 0, cameras: 0,
  cameras_online: 0, crit: 0, warn: 0, sensor_ok: true, is_primary: true, ...over,
});

describe("the tile row and the network chooser", () => {
  it("re-reads the tiles for the network the chooser selects", async () => {
    const nets = [health({}), health({ id: "net_b", slug: "b", name: "Lab", is_primary: false })];
    vi.mocked(devicesPayload).mockImplementation((network?: string | null) =>
      Promise.resolve(payload({
        networks: nets,
        online: network === "net_b" ? 2 : 19,
        total: network === "net_b" ? 5 : 25,
        tiles: tiles({ trusted: network === "net_b" ? 1 : 12 }),
      })),
    );

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ToastProvider>
          <MemoryRouter initialEntries={["/assets/devices"]}>
            <Routes>
              <Route path="/assets/:view" element={<Assets />} />
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </QueryClientProvider>,
    );

    await screen.findByTestId("device-tiles");
    // The row renders immediately from the page's empty default, so wait for
    // the fetched payload rather than asserting on the first paint.
    await vi.waitFor(() => expect(valueOf("Trusted")).toBe("12"));

    const chips = await screen.findByTestId("network-chips");
    const lab = [...chips.querySelectorAll("button")].find((b) => b.textContent?.includes("Lab"));
    lab?.click();

    await vi.waitFor(() => expect(valueOf("Trusted")).toBe("1"));
    expect(valueOf("Devices online")).toBe("2");
    expect(unitOf("Devices online")).toBe("/ 5 known");
  });
});
