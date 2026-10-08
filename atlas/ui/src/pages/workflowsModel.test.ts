import { describe, expect, it } from "vitest";
import {
  assignLayers, CARD_H, CARD_W, dotOutcome, KIND_GLYPH, layout,
  MAX_ZOOM, MIN_ZOOM, nextZoom,
} from "./workflowsModel";

function node(id: string, kind: "trigger" | "check" | "gate" | "action" = "check") {
  return { id, kind };
}

describe("assignLayers: longest path, not shortest", () => {
  it("a node with no incoming edge is column 0", () => {
    const layers = assignLayers({ nodes: [node("a")], edges: [] });
    expect(layers.get("a")).toBe(0);
  });

  it("a straight chain increases by one each hop", () => {
    const layers = assignLayers({
      nodes: [node("a"), node("b"), node("c")],
      edges: [{ from: "a", to: "b", label: "" }, { from: "b", to: "c", label: "" }],
    });
    expect(layers.get("a")).toBe(0);
    expect(layers.get("b")).toBe(1);
    expect(layers.get("c")).toBe(2);
  });

  it("a node reachable by a short and a long path takes the long one", () => {
    // a -> d (one hop) and a -> b -> c -> d (three hops): d must sit past
    // c, not right after a, or it would visually precede work it depends on.
    const layers = assignLayers({
      nodes: [node("a"), node("b"), node("c"), node("d")],
      edges: [
        { from: "a", to: "d", label: "" },
        { from: "a", to: "b", label: "" },
        { from: "b", to: "c", label: "" },
        { from: "c", to: "d", label: "" },
      ],
    });
    expect(layers.get("d")).toBe(3);
  });

  it("an edge naming a node outside the graph is ignored, not a crash", () => {
    const layers = assignLayers({
      nodes: [node("a")],
      edges: [{ from: "a", to: "ghost", label: "" }],
    });
    expect(layers.get("a")).toBe(0);
  });

  it("a cycle terminates instead of recursing forever", () => {
    const layers = assignLayers({
      nodes: [node("a"), node("b")],
      edges: [{ from: "a", to: "b", label: "" }, { from: "b", to: "a", label: "" }],
    });
    expect(layers.get("a")).toBeDefined();
    expect(layers.get("b")).toBeDefined();
  });
});

describe("layout: one card per node, a diamond for every gate", () => {
  const graph = {
    nodes: [
      node("trigger", "trigger"),
      node("checkit", "check"),
      node("askit", "gate"),
      node("doit", "action"),
    ],
    edges: [
      { from: "trigger", to: "checkit", label: "" },
      { from: "checkit", to: "askit", label: "" },
      { from: "askit", to: "doit", label: "approved" },
    ],
  };

  it("draws exactly one node per catalog node", () => {
    const out = layout(graph);
    expect(out.nodes).toHaveLength(graph.nodes.length);
    expect(new Set(out.nodes.map((n) => n.id)).size).toBe(graph.nodes.length);
  });

  it("only a gate is a diamond; everything else is a card", () => {
    const out = layout(graph);
    const byId = new Map(out.nodes.map((n) => [n.id, n]));
    expect(byId.get("askit")?.shape).toBe("diamond");
    for (const id of ["trigger", "checkit", "doit"]) {
      expect(byId.get(id)?.shape).toBe("card");
    }
  });

  it("is deterministic: the same graph lays out identically every time", () => {
    const a = layout(graph);
    const b = layout(graph);
    expect(a).toEqual(b);
  });

  it("lays out strictly left to right along the chain", () => {
    const out = layout(graph);
    const byId = new Map(out.nodes.map((n) => [n.id, n]));
    const [xt, xc, xa, xd] = ["trigger", "checkit", "askit", "doit"]
      .map((id) => byId.get(id)!.x);
    expect(xt!).toBeLessThan(xc!);
    expect(xc!).toBeLessThan(xa!);
    expect(xa!).toBeLessThan(xd!);
  });

  it("keeps ties in catalog order within a column, never resorted", () => {
    const fan = {
      nodes: [node("t", "trigger"), node("z"), node("a"), node("m")],
      edges: [
        { from: "t", to: "z", label: "" },
        { from: "t", to: "a", label: "" },
        { from: "t", to: "m", label: "" },
      ],
    };
    const out = layout(fan);
    const byId = new Map(out.nodes.map((n) => [n.id, n]));
    // z, a, m in that order (as given), not alphabetised to a, m, z.
    expect(byId.get("z")!.y).toBeLessThan(byId.get("a")!.y);
    expect(byId.get("a")!.y).toBeLessThan(byId.get("m")!.y);
  });

  it("every edge's path starts and ends at its nodes' card edges", () => {
    const out = layout(graph);
    const byId = new Map(out.nodes.map((n) => [n.id, n]));
    const e = out.edges.find((e) => e.from === "checkit" && e.to === "askit")!;
    const from = byId.get("checkit")!;
    const to = byId.get("askit")!;
    expect(e.d.startsWith(`M ${from.x + CARD_W} ${from.y + CARD_H / 2}`)).toBe(true);
    expect(e.d.endsWith(`${to.x} ${to.y + CARD_H / 2}`)).toBe(true);
  });

  it("carries the edge label through untouched", () => {
    const out = layout(graph);
    expect(out.edges.find((e) => e.to === "doit")?.label).toBe("approved");
  });

  it("sizes the canvas to fit every column and the tallest one", () => {
    const out = layout(graph);
    expect(out.width).toBeGreaterThan(0);
    expect(out.height).toBeGreaterThan(0);
  });
});

describe("dotOutcome: unknown draws exactly like never (invariant 7)", () => {
  it.each([
    ["ok", "ok"], ["degraded", "degraded"], ["failed", "failed"],
    ["never", "never"], ["unknown", "never"],
  ] as const)("%s -> %s", (outcome, want) => {
    expect(dotOutcome(outcome)).toBe(want);
  });
});

describe("KIND_GLYPH: every one of the seven kinds has its own glyph", () => {
  it("covers all seven and none are blank or duplicated", () => {
    const kinds = ["trigger", "check", "agent", "action", "gate", "channel",
                   "store"] as const;
    const glyphs = kinds.map((k) => KIND_GLYPH[k]);
    expect(glyphs.every((g) => g.length > 0)).toBe(true);
    expect(new Set(glyphs).size).toBe(kinds.length);
  });
});

describe("nextZoom: clamped, and direction matches scroll", () => {
  it("scrolling up (negative deltaY) zooms in", () => {
    expect(nextZoom(1, -100)).toBeGreaterThan(1);
  });

  it("scrolling down (positive deltaY) zooms out", () => {
    expect(nextZoom(1, 100)).toBeLessThan(1);
  });

  it("never exceeds MAX_ZOOM however hard the scroll", () => {
    expect(nextZoom(1, -100000)).toBeLessThanOrEqual(MAX_ZOOM);
  });

  it("never drops below MIN_ZOOM however hard the scroll", () => {
    expect(nextZoom(1, 100000)).toBeGreaterThanOrEqual(MIN_ZOOM);
  });

  it("a zero delta leaves the zoom unchanged", () => {
    expect(nextZoom(1.3, 0)).toBeCloseTo(1.3);
  });
});
