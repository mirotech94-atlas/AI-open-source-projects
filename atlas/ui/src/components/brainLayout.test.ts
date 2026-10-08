/**
 * The Brain map's geometry, and its markup: every department and seat is
 * placed inside the frame, the core sits at the centre, YOU is wired only to
 * departments with a Tier 2 action, and the phone frame is a stack no wider
 * than its own width.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { brainFixture } from "../pages/brainFixture";
import { BrainMap } from "./BrainMap";
import { BRAIN_COMPACT, BRAIN_WIDE, brainLayout, fan, seatSize } from "./brainLayout";

describe("brainLayout", () => {
  for (const variant of ["wide", "compact"] as const) {
    it(`places every department and seat inside the ${variant} frame`, () => {
      const b = brainFixture();
      const l = brainLayout(b, variant);
      const seatCount = b.departments.reduce((n, d) => n + d.agents.length + d.jobs.length, 0);
      expect(l.seats).toHaveLength(seatCount);
      expect(l.depts).toHaveLength(b.departments.filter((d) => !d.core).length);
      for (const n of [l.you, l.core, ...l.depts, ...l.seats]) {
        expect(n.px).toBeGreaterThanOrEqual(0);
        expect(n.px).toBeLessThanOrEqual(100);
        expect(n.py).toBeGreaterThanOrEqual(0);
        expect(n.py).toBeLessThanOrEqual(100);
      }
      const ids = [...l.byId.keys()];
      expect(new Set(ids).size).toBe(ids.length);
    });
  }

  it("puts the core at the centre and YOU above it", () => {
    const l = brainLayout(brainFixture(), "wide");
    expect([l.core.x, l.core.y]).toEqual([BRAIN_WIDE.cx, BRAIN_WIDE.cy]);
    expect(l.you.y).toBeLessThan(l.core.y);
    expect(l.core.id).toBe("dept:knowledge");
  });

  it("wires YOU only to departments with a Tier 2 action", () => {
    const l = brainLayout(brainFixture(), "wide");
    const signoff = l.links.filter((x) => x.kind === "signoff").map((x) => x.dept);
    expect(signoff).toEqual(["money"]);
    const spokes = l.links.filter((x) => x.kind === "spoke").map((x) => x.dept).sort();
    expect(spokes).toEqual(["money", "ops", "strategy"]);
  });

  it("the phone frame is the phone's own width, taller with more rows", () => {
    const l = brainLayout(brainFixture(), "compact");
    expect(l.frame.w).toBe(BRAIN_COMPACT.w);
    const lowest = Math.max(...l.seats.map((s) => s.y), ...l.depts.map((d) => d.y));
    expect(l.frame.h).toBeGreaterThan(lowest);
  });

  it("sizes seats by runs, and fans them evenly", () => {
    expect(seatSize(null)).toBe(7);
    expect(seatSize(0)).toBe(7);
    expect(seatSize(10_000)).toBe(16);
    expect(fan(3, 0, 0.5)).toEqual([-0.5, 0, 0.5]);
  });
});

describe("BrainMap", () => {
  const html = renderToStaticMarkup(createElement(BrainMap, { brain: brainFixture(), variant: "wide" }));

  it("draws one focusable node per department, seat, the core and YOU", () => {
    const nodes = html.match(/class="nn-node bn-node"/g) ?? [];
    expect(nodes.length).toBe(1 + 1 + 3 + 7);
    expect(html).toContain('data-kind="you"');
    expect(html).toMatch(/role="button"/);
  });

  it("a seat with no per-run record is drawn unknown, not idle", () => {
    expect(html).toMatch(/data-dept="money" data-ran="unknown" data-layer="automation"/);
    expect(html).toMatch(/data-dept="ops" data-ran="idle" data-layer="agent"/);
  });

  it("a department with no executions reads unknown on the map", () => {
    expect(html).toMatch(/data-kind="dept" data-dept="ops" data-ran="unknown"/);
    expect(html).toContain("Marcus · unknown");
    expect(html).toContain("Ivan · 75%");
  });

  it("draws the sign-off link and says what it means", () => {
    expect(html).toMatch(/<g class="bn-signoff"><path [^>]*data-dept="money"/);
    expect(html).toContain("Needs your sign-off (Tier 2)");
  });
});
