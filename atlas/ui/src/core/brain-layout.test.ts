import { describe, expect, it } from "vitest";
import {
  BRAIN_ANCHOR,
  REGION_ORDER,
  brainLayout,
  regionLabel,
  type BrainRegion,
} from "./brain-layout";

function region(over: Partial<BrainRegion> = {}): BrainRegion {
  return {
    region: "PREFRONTAL",
    namespaces: ["commitments"],
    memories: 4,
    last_read_at: null,
    reads_1h: 0,
    firing: 0,
    ...over,
  };
}

describe("regionLabel", () => {
  it("says quiet when nothing has read that region", () => {
    expect(regionLabel(region({ memories: 12, last_read_at: null })))
      .toBe("12 memories · quiet");
  });

  it("says 0 memories, and quiet, for a region with nothing stored", () => {
    expect(regionLabel(region({ memories: 0, last_read_at: null })))
      .toBe("0 memories · quiet");
  });

  /** The clock is the reader's own, so the hour is not asserted — the shape
   *  is, because a label that silently lost its time is the bug. */
  it("carries the time of the last read when there was one", () => {
    const label = regionLabel(region({
      memories: 31, last_read_at: "2026-09-24T11:07:00+00:00",
    }));
    expect(label).toMatch(/^31 memories · fired \d{2}:\d{2}$/);
  });

  it("does not invent a time out of an unparseable stamp", () => {
    expect(regionLabel(region({ memories: 3, last_read_at: "not a date" })))
      .toBe("3 memories · quiet");
  });
});

describe("brainLayout", () => {
  const input: BrainRegion[] = [
    region({ region: "PREFRONTAL", memories: 10, reads_1h: 4, firing: 1 }),
    region({ region: "HIPPOCAMPUS", memories: 40, reads_1h: 2, firing: 0.5 }),
    region({ region: "MOTOR", memories: 0, reads_1h: 0, firing: 0 }),
  ];

  it("places every region inside the box it was given", () => {
    const nodes = brainLayout(input, 300, 220);
    expect(nodes).toHaveLength(3);
    for (const node of nodes) {
      expect(node.x - node.r).toBeGreaterThanOrEqual(0);
      expect(node.x + node.r).toBeLessThanOrEqual(300);
      expect(node.y - node.r).toBeGreaterThanOrEqual(0);
      expect(node.y + node.r).toBeLessThanOrEqual(220);
    }
  });

  it("keeps a region's own position wherever else it appears in the list", () => {
    const forwards = brainLayout(input, 300, 220);
    const backwards = brainLayout([...input].reverse(), 300, 220);
    const find = (nodes: typeof forwards, name: string) =>
      nodes.find((n) => n.region === name);
    for (const name of ["PREFRONTAL", "HIPPOCAMPUS", "MOTOR"]) {
      expect(find(backwards, name)?.x).toBe(find(forwards, name)?.x);
      expect(find(backwards, name)?.y).toBe(find(forwards, name)?.y);
    }
  });

  it("sizes by memories on a sqrt scale, and an empty region is still drawn", () => {
    const nodes = brainLayout(input, 300, 220);
    const pre = nodes.find((n) => n.region === "PREFRONTAL");
    const hip = nodes.find((n) => n.region === "HIPPOCAMPUS");
    const motor = nodes.find((n) => n.region === "MOTOR");
    expect(hip!.r).toBeGreaterThan(pre!.r);
    expect(motor!.r).toBeGreaterThan(0);
    expect(hip!.r).toBeLessThan(4 * motor!.r);
  });

  it("carries the label and the firing level through untouched", () => {
    const nodes = brainLayout(input, 300, 220);
    const motor = nodes.find((n) => n.region === "MOTOR");
    expect(motor!.label).toBe("0 memories · quiet");
    expect(motor!.firing).toBe(0);
    expect(nodes.find((n) => n.region === "PREFRONTAL")!.firing).toBe(1);
  });

  it("clamps a firing figure the server could not have meant", () => {
    const nodes = brainLayout([region({ firing: 9 }), region({
      region: "MOTOR", firing: Number.NaN,
    })], 300, 220);
    expect(nodes[0]!.firing).toBe(1);
    expect(nodes[1]!.firing).toBe(0);
  });

  it("gives a region the server invented a place rather than dropping it", () => {
    const nodes = brainLayout([region({ region: "CEREBELLUM" })], 300, 220);
    expect(nodes).toHaveLength(1);
    expect(Number.isFinite(nodes[0]!.x)).toBe(true);
    expect(Number.isFinite(nodes[0]!.y)).toBe(true);
  });

  it("knows a place for every region the API can name", () => {
    const nodes = brainLayout(
      REGION_ORDER.map((name) => region({ region: name })), 400, 300);
    const seen = new Set(nodes.map((n) => `${n.x},${n.y}`));
    // No two regions share a spot, or two clusters would sit on top of one
    // another and the map would read as one blob.
    expect(seen.size).toBe(REGION_ORDER.length);
  });

  it("anchors every edge at the same point on the bust's head", () => {
    expect(BRAIN_ANCHOR.x).toBeGreaterThanOrEqual(0);
    expect(BRAIN_ANCHOR.y).toBeGreaterThanOrEqual(0);
    const nodes = brainLayout(input, 300, 220);
    for (const node of nodes) {
      expect(node.anchor).toEqual(BRAIN_ANCHOR);
    }
  });

  it("returns nothing for nothing, rather than a default brain", () => {
    expect(brainLayout([], 300, 220)).toEqual([]);
  });
});
