/** The header's per-site sparkline must keep its own 88x22 box. It used to
 *  carry the same `g-spark` class as the design primitive in
 *  `design/Sparkline.tsx`, and `design/primitives.css` sizes that one at
 *  `width: 100%` -- which beat the header SVG's `width="52"` attribute,
 *  stretched it to the whole pill, collapsed the site name to nothing and
 *  pushed the figure out over the next pill. Visible in production. */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TopBar } from "../src/shell/TopBar";
import type { LivePayload } from "../src/types";

const live: LivePayload = {
  ts: "2026-09-14T12:00:00+00:00",
  sites: [
    { name: "Roots prod (status.example.org)", url: "https://status.example.org", ok: true, ms: 690, code: 200, spark: [640, 700, 660, 690] },
  ],
  scanning: false,
  sensor_age_s: 45,
  load1: 9.16,
  cores: 10,
  maintenance_until: null,
};

describe("the header latency sparkline", () => {
  it("does not share the design primitive's `g-spark` class, and keeps its 88x22 box", () => {
    render(
      <TopBar
        me={null} role="admin" overview={null} overviewFetchedAt={null} live={live}
        scanState="idle" maintenanceUntil={null} advanced={false}
        onToggleAdvanced={() => {}} theme="dark" onCycleTheme={() => {}} onOpenPalette={() => {}}
        onOpenWallboard={() => {}}
        onRefresh={() => {}} onScanNow={() => {}} onSpeedtest={() => {}} onMaintenance={() => {}}
        onSignOut={() => {}}
      />,
    );
    const strip = screen.getByLabelText("Site response times");
    const svg = strip.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg!.classList.contains("g-spark")).toBe(false);
    expect(svg!.classList.contains("g-latency-spark")).toBe(true);
    expect(svg!.getAttribute("width")).toBe("88");
    expect(svg!.getAttribute("height")).toBe("22");
  });
});
