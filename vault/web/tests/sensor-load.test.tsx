/** Task 70: the sub-header's sensor-host load figure, and the freshness pill it
 *  falls back to. */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TopBar } from "../src/shell/TopBar";
import type { LivePayload } from "../src/types";

const live = (over: Partial<LivePayload> = {}): LivePayload => ({
  ts: "2026-09-10T12:00:00+00:00",
  sites: [{ name: "Roots prod (status.example.org)", url: "https://status.example.org", ok: true, ms: 120, code: 200, spark: [] }],
  scanning: false,
  sensor_age_s: 45,
  load1: null,
  cores: null,
  maintenance_until: null,
  ...over,
});

function renderTopBar(payloadIn: LivePayload) {
  return render(
    <TopBar
      me={null} role="admin" overview={null} overviewFetchedAt={null} live={payloadIn}
      scanState="idle" maintenanceUntil={null} advanced={false}
      onToggleAdvanced={() => {}} theme="dark" onCycleTheme={() => {}} onOpenPalette={() => {}}
      onOpenWallboard={() => {}}
      onRefresh={() => {}} onScanNow={() => {}} onSpeedtest={() => {}} onMaintenance={() => {}}
      onSignOut={() => {}}
    />,
  );
}

describe("the sensor-host load figure", () => {
  it("renders the load average and the core count in the sensor slot", () => {
    renderTopBar(live({ load1: 0.06, cores: 8 }));
    const pill = screen.getByTestId("sensor-load-pill");
    expect(pill.querySelector("b")?.textContent).toBe("0.06");
    expect(pill.textContent).toContain("/ 8 cores");
    expect(pill.title).toContain("8 cores");
    expect(screen.queryByTestId("sensor-freshness-pill")).toBeNull();
  });

  it("keeps the number two-decimal, so a whole load does not read as an integer count", () => {
    renderTopBar(live({ load1: 2, cores: 4 }));
    expect(screen.getByTestId("sensor-load-pill").querySelector("b")?.textContent).toBe("2.00");
  });

  it("falls back to the freshness pill on a sensor that reports no load", () => {
    renderTopBar(live({ load1: null, cores: null }));
    expect(screen.getByTestId("sensor-freshness-pill").textContent).toContain("45s");
    expect(screen.queryByTestId("sensor-load-pill")).toBeNull();
  });

  it("still renders the load when the host will not say how many cores it has", () => {
    renderTopBar(live({ load1: 1.5, cores: null }));
    const pill = screen.getByTestId("sensor-load-pill");
    expect(pill.querySelector("b")?.textContent).toBe("1.50");
    expect(pill.textContent).not.toContain("cores");
  });
});
