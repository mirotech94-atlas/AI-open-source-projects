import { describe, expect, it } from "vitest";
import type { AgentSummary } from "../api";
import {
  COMPACT_BELOW_PX, PULSE_WINDOW_MS, WIDE_FRAME, cardName, isPulsing, neuralLayout,
  neuronSize, shortName, sideFor, synapsePairs, variantFor,
} from "./neuralLayout";

function agent(over: Partial<AgentSummary> = {}): AgentSummary {
  return {
    name: "ops", title: "Ops", display: "Ops", brief: "infra", need: null,
    available: true, providers: [], tools: 3, last_run_at: null,
    last_outcome: null, runs_24h: 0, model: null, model_at: null,
    handoff: [], memory_scope: [], ready: true, reasons: [],
    group: "functions", next_shift_at: null,
    ...over,
  };
}

const roster = [
  agent({ name: "concierge", group: "lead", handoff: ["ops", "flycheap", "ghost"],
          memory_scope: ["people"], runs_24h: 31 }),
  agent({ name: "flycheap", group: "businesses", handoff: ["ops", "analyst"],
          memory_scope: ["flycheap", "incidents"], runs_24h: 8 }),
  agent({ name: "guardian", group: "businesses", handoff: ["ops"],
          memory_scope: ["incidents", "security"] }),
  agent({ name: "ops", group: "functions", handoff: ["concierge"],
          memory_scope: ["incidents"] }),
  agent({ name: "analyst", group: "functions", memory_scope: ["finance"] }),
  agent({ name: "inbox", group: "functions", memory_scope: ["people"] }),
];

describe("neuronSize -- runs in the last 24 hours", () => {
  it("an agent that has not run is the smallest neuron", () => {
    expect(neuronSize(0)).toBe(8);
    expect(neuronSize(-3)).toBe(8);
    expect(neuronSize(Number.NaN)).toBe(8);
  });
  it("grows with runs, but by the square root, and is capped", () => {
    expect(neuronSize(4)).toBeGreaterThan(neuronSize(1));
    expect(neuronSize(9) - neuronSize(4)).toBeLessThan(neuronSize(4) - neuronSize(0));
    expect(neuronSize(10_000)).toBe(22);
  });
});

describe("synapsePairs -- only declared relationships, once per pair", () => {
  const pairs = synapsePairs(roster);
  const find = (a: string, b: string) =>
    pairs.find((p) => (p.a === a && p.b === b) || (p.a === b && p.b === a));

  it("a handoff in both directions is one synapse", () => {
    expect(pairs.filter((p) => find("concierge", "ops") === p)).toHaveLength(1);
    expect(find("concierge", "ops")?.kind).toBe("handoff");
  });
  it("handoff beats shared memory for the same pair", () => {
    // flycheap and ops share "incidents" AND flycheap hands off to ops.
    expect(find("flycheap", "ops")?.kind).toBe("handoff");
  });
  it("shared memory with no handoff is a memory synapse", () => {
    expect(find("flycheap", "guardian")?.kind).toBe("memory");
    expect(find("concierge", "inbox")?.kind).toBe("memory");
  });
  it("no synapse where nothing is declared", () => {
    expect(find("analyst", "inbox")).toBeUndefined();
    expect(find("guardian", "analyst")).toBeUndefined();
  });
  it("a handoff to an agent the roster does not hold is dropped", () => {
    expect(pairs.some((p) => p.a === "ghost" || p.b === "ghost")).toBe(false);
  });
  it("never a self-synapse", () => {
    const self = synapsePairs([agent({ name: "a", handoff: ["a"], memory_scope: ["x"] })]);
    expect(self).toEqual([]);
  });
  it("is independent of roster order", () => {
    expect(synapsePairs([...roster].reverse())).toEqual(pairs);
  });
});

describe("neuralLayout -- wide frame", () => {
  const layout = neuralLayout(roster, "wide");

  it("places every agent exactly once", () => {
    expect([...layout.byName.keys()].sort()).toEqual(roster.map((a) => a.name).sort());
    expect(layout.byName.size).toBe(roster.length);
  });
  it("the lead sits at 12 o'clock on the inner ring, above the core", () => {
    expect(layout.lead?.agent.name).toBe("concierge");
    expect(layout.lead!.x).toBeCloseTo(WIDE_FRAME.cx);
    expect(layout.lead!.y).toBeCloseTo(WIDE_FRAME.cy - WIDE_FRAME.inner.ry);
  });
  it("businesses on the inner ring, functions on the outer", () => {
    const dist = (name: string) => {
      const n = layout.byName.get(name)!;
      return Math.hypot((n.x - WIDE_FRAME.cx) / WIDE_FRAME.outer.rx,
                        (n.y - WIDE_FRAME.cy) / WIDE_FRAME.outer.ry);
    };
    expect(dist("flycheap")).toBeLessThan(dist("ops"));
    expect(dist("ops")).toBeCloseTo(1);
  });
  it("positions are percentages of the frame, inside it", () => {
    for (const n of layout.byName.values()) {
      expect(n.px).toBeGreaterThanOrEqual(0);
      expect(n.px).toBeLessThanOrEqual(100);
      expect(n.py).toBeGreaterThanOrEqual(0);
      expect(n.py).toBeLessThanOrEqual(100);
    }
  });
  it("outer labels read outward, left or right; inner labels above or below", () => {
    for (const n of layout.functions) {
      expect(n.side).toBe(n.x < WIDE_FRAME.cx ? "left" : "right");
    }
    for (const n of layout.businesses) expect(["top", "bottom"]).toContain(n.side);
    expect(layout.lead!.side).toBe("top");
  });
  it("one spoke per agent, each ending on the core", () => {
    expect(layout.spokes).toHaveLength(roster.length);
    for (const s of layout.spokes) {
      expect(s.d.endsWith(`L${WIDE_FRAME.cx},${WIDE_FRAME.cy}`)).toBe(true);
    }
  });
  it("draws one path per declared pair, and none to an absent agent", () => {
    expect(layout.synapses.map((s) => `${s.a}|${s.b}`))
      .toEqual(synapsePairs(roster).map((p) => `${p.a}|${p.b}`));
  });
  it("is deterministic: the same roster in any order lays out identically", () => {
    const again = neuralLayout([...roster].reverse(), "wide");
    expect(again.synapses).toEqual(layout.synapses);
    expect([...again.byName.values()].map((n) => [n.agent.name, n.x, n.y]).sort())
      .toEqual([...layout.byName.values()].map((n) => [n.agent.name, n.x, n.y]).sort());
  });
  it("a synapse between opposite neurons bends around the core", () => {
    const opposite = [
      agent({ name: "w", group: "functions", handoff: ["y"] }),
      agent({ name: "x", group: "functions" }),
      agent({ name: "y", group: "functions" }),
      agent({ name: "z", group: "functions" }),
    ];
    const l = neuralLayout(opposite, "wide");
    const s = l.synapses.find((x) => x.a === "w" && x.b === "y")!;
    const m = /Q([-\d.]+),([-\d.]+)/.exec(s.d)!;
    const dist = Math.hypot(Number(m[1]) - WIDE_FRAME.cx, Number(m[2]) - WIDE_FRAME.cy);
    expect(dist).toBeGreaterThan(60);
  });
  it("an empty roster lays out nothing, without throwing", () => {
    const empty = neuralLayout([], "wide");
    expect(empty.lead).toBeNull();
    expect(empty.byName.size).toBe(0);
    expect(empty.synapses).toEqual([]);
  });
});

describe("neuralLayout -- compact (phone) frame", () => {
  const many = [
    agent({ name: "concierge", group: "lead" }),
    ...Array.from({ length: 5 }, (_, i) => agent({ name: `b${i}`, group: "businesses" })),
    ...Array.from({ length: 9 }, (_, i) => agent({ name: `f${i}`, group: "functions" })),
  ];
  const layout = neuralLayout(many, "compact");

  it("layers top to bottom: core, lead, businesses, functions", () => {
    const y = (name: string) => layout.byName.get(name)!.y;
    expect(layout.frame.cy).toBeLessThan(y("concierge"));
    expect(y("concierge")).toBeLessThan(y("b0"));
    expect(Math.max(...layout.businesses.map((n) => n.y)))
      .toBeLessThan(Math.min(...layout.functions.map((n) => n.y)));
  });
  it("at most four neurons to a row, every label under its neuron", () => {
    const rows = new Map<number, number>();
    for (const n of layout.byName.values()) rows.set(n.y, (rows.get(n.y) ?? 0) + 1);
    expect(Math.max(...rows.values())).toBeLessThanOrEqual(4);
    for (const n of layout.byName.values()) expect(n.side).toBe("bottom");
  });
  it("grows taller with more rows, and every neuron stays inside the frame", () => {
    expect(layout.frame.h).toBeGreaterThan(neuralLayout(many.slice(0, 3), "compact").frame.h);
    for (const n of layout.byName.values()) {
      expect(n.py).toBeGreaterThan(0);
      expect(n.py).toBeLessThan(100);
      expect(n.px).toBeGreaterThan(0);
      expect(n.px).toBeLessThan(100);
    }
  });
  it("gives each lane's title a place above its band", () => {
    expect(layout.bands).not.toBeNull();
    expect(layout.bands!.businesses).toBeLessThan(layout.bands!.functions);
    expect(neuralLayout(many, "wide").bands).toBeNull();
  });
});

describe("variantFor", () => {
  it("compact below the threshold, wide at or above it; unmeasured is wide", () => {
    expect(variantFor(343)).toBe("compact");
    expect(variantFor(COMPACT_BELOW_PX)).toBe("wide");
    expect(variantFor(1200)).toBe("wide");
    expect(variantFor(0)).toBe("wide");
  });
});

describe("isPulsing -- a signal only for a real, recent run", () => {
  const now = new Date("2026-09-28T12:00:00Z").getTime();
  const at = (msAgo: number) => new Date(now - msAgo).toISOString();

  it("a run under ten minutes old pulses", () => {
    expect(isPulsing({ last_run_at: at(30_000) }, false, now)).toBe(true);
    expect(isPulsing({ last_run_at: at(PULSE_WINDOW_MS - 1000) }, false, now)).toBe(true);
  });
  it("ten minutes or older does not", () => {
    expect(isPulsing({ last_run_at: at(PULSE_WINDOW_MS) }, false, now)).toBe(false);
    expect(isPulsing({ last_run_at: at(3_600_000) }, false, now)).toBe(false);
  });
  it("never run, a future stamp or garbage never pulses", () => {
    expect(isPulsing({ last_run_at: null }, false, now)).toBe(false);
    expect(isPulsing({ last_run_at: at(-60_000) }, false, now)).toBe(false);
    expect(isPulsing({ last_run_at: "not a date" }, false, now)).toBe(false);
  });
  it("a failed roster read never pulses: unknown never glows", () => {
    expect(isPulsing({ last_run_at: at(1000) }, true, now)).toBe(false);
  });
  it("readiness does not hide a real run: a not-ready agent that just ran still pulses", () => {
    // isPulsing takes no readiness at all -- by design (see its doc).
    expect(isPulsing(agent({ ready: false, last_run_at: at(1000) }), false, now)).toBe(true);
  });
});

describe("names", () => {
  const persona = { agent: "ops", name: "Marcus", specialty: "infra", voice: "male" as const };
  it("persona name and display, or the display alone", () => {
    expect(cardName({ display: "Operations", persona })).toBe("Marcus · Operations");
    expect(cardName({ display: "Operations" })).toBe("Operations");
  });
  it("a derived persona named after its title is never said twice", () => {
    expect(cardName({ display: "Nightshift",
                      persona: { ...persona, name: "Nightshift" } })).toBe("Nightshift");
  });
  it("the short name is the persona's first name, else the display", () => {
    expect(shortName({ display: "Operations", persona })).toBe("Marcus");
    expect(shortName({ display: "Operations" })).toBe("Operations");
  });
});

describe("sideFor", () => {
  it("left and right along the line, top and bottom near the poles", () => {
    expect(sideFor(10, 0)).toBe("right");
    expect(sideFor(-10, 1)).toBe("left");
    expect(sideFor(1, -10)).toBe("top");
    expect(sideFor(-1, 10)).toBe("bottom");
  });
});
