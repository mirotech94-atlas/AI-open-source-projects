/** The header, combined from two of the layout options the owner picked:
 *  the bar carries a centre status cluster (live dot, last scan, scans/hr,
 *  sensor load), and the site strip is a rail of labelled columns -- one
 *  per site with its figure and sparkline, plus "updated" at the end. */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TopBar } from "../src/shell/TopBar";
import type { LivePayload, OverviewPayload } from "../src/types";

const live: LivePayload = {
  ts: "2026-09-15T12:00:00+00:00",
  sites: [
    { name: "Roots prod (status.example.org)", url: "https://status.example.org", ok: true, ms: 409, code: 200, spark: [380, 420, 409] },
    { name: "FlyCheap", url: "https://flycheap.example", ok: false, ms: 93, code: 502, spark: [88, 140, 93] },
  ],
  scanning: false,
  sensor_age_s: 45,
  load1: 9.16,
  cores: 10,
  maintenance_until: null,
};

function renderTopBar(payload: LivePayload | null, overview: OverviewPayload | null = null) {
  return render(
    <TopBar
      me={null} role="admin" overview={overview} overviewFetchedAt={null} live={payload}
      scanState="idle" maintenanceUntil={null} advanced={false}
      onToggleAdvanced={() => {}} theme="dark" onCycleTheme={() => {}} onOpenPalette={() => {}}
      onOpenWallboard={() => {}}
      onRefresh={() => {}} onScanNow={() => {}} onSpeedtest={() => {}} onMaintenance={() => {}}
      onSignOut={() => {}}
    />,
  );
}

describe("header layout", () => {
  it("puts the live dot, scan age and sensor load in a centre cluster inside the bar", () => {
    renderTopBar(live);
    const status = screen.getByTestId("topbar-status");
    expect(status.closest("header")).not.toBeNull();
    expect(status.querySelector(".g-pulse-dot")).not.toBeNull();
    // No overview has answered yet: the bar must not claim there is no scan.
    expect(status.textContent).toContain("checking…");
    expect(status.textContent).not.toContain("no scan yet");
    const load = within(status).getByTestId("sensor-load-pill");
    expect(load.querySelector("b")?.textContent).toBe("9.16");
    expect(load.textContent).toContain("/ 10 cores");
  });

  it("says no scan yet only once the overview has answered without one", () => {
    renderTopBar(live, { age_s: null, health: { scans_last_hour: 0 }, sensor: null } as unknown as OverviewPayload);
    expect(screen.getByTestId("topbar-status").textContent).toContain("no scan yet");
  });

  it("lays the sites out as labelled columns with figure and sparkline, and ends with updated", () => {
    renderTopBar(live);
    const rail = screen.getByLabelText("Site response times");
    expect(rail.closest("header")).toBeNull();
    const cols = within(rail).getAllByTestId("site-column");
    expect(cols).toHaveLength(2);
    expect(cols[0].querySelector(".g-rail-label")?.textContent).toBe("Roots prod");
    expect(cols[0].querySelector(".g-rail-label")?.getAttribute("title")).toBe("Roots prod (status.example.org)");
    expect(cols[0].querySelector(".g-rail-figure")?.textContent).toContain("409");
    expect(cols[0].querySelector("svg.g-latency-spark")).not.toBeNull();
    expect(cols[1].className).toContain("g-tone-alert");
    // Nothing in the rail carries the sensor figures any more -- those live in the bar.
    expect(within(rail).queryByTestId("sensor-load-pill")).toBeNull();
    expect(within(rail).getByTestId("rail-updated").textContent).toMatch(/updated/i);
  });

  it("renders no rail without sites, but keeps the centre cluster", () => {
    renderTopBar({ ...live, sites: [] });
    expect(screen.queryByLabelText("Site response times")).toBeNull();
    expect(screen.getByTestId("topbar-status")).toBeInTheDocument();
  });
});
