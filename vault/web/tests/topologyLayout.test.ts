// Item 17: the map's geometry, which no test held before.
//
// What broke: a segment was ONE row however many devices it had. The demo's
// 19-device Office Wi-Fi measured 2210 by 422 layout units, so fitting it
// into a 734x520 box scaled every tile down to a few pixels — a map you pan
// across one row at a time is a list drawn sideways, not a picture of a
// network. Leaves now wrap, and these pin the properties that matter.
import { describe, expect, it } from "vitest";
import { computeLayout } from "../src/lib/topologyLayout";
import type { TopoNode, TopologyPayload } from "../src/types";

const net = { id: "net-a", slug: "n", name: "Office", colour: "" };

function node(i: number, over: Partial<TopoNode> = {}): TopoNode {
  return {
    id: `d${i}`, kind: "device", group: "Computers", label: `Device ${i}`,
    ip: `192.168.1.${10 + i}`, mac: `aa:bb:cc:00:00:${String(i).padStart(2, "0")}`,
    icon: "laptop", state: "online", risk: 0, ring: 3, trusted: true,
    quarantined: false, pulse: false, network: net, ...over,
  } as TopoNode;
}

/** `count` leaves on one network, each distinct enough not to be bundled. */
function payload(count: number): TopologyPayload {
  // Every leaf carries risk >= 40, which `isAlwaysIndividual` never folds
  // into a bundle -- so this measures the WRAP, not the collapse. (The
  // first version of this fixture let the bundler turn 24 devices into 10
  // chips and the wrap never fired.)
  const leaves = Array.from({ length: count }, (_, i) => node(i, { risk: 55 }));
  const gateway: TopoNode = node(999, {
    id: "gw", kind: "gateway", label: "Router", ring: 1, icon: "router", group: "Network",
  });
  const internet: TopoNode = node(998, {
    id: "internet", kind: "internet", label: "Internet", ring: 0, icon: "internet",
    group: "Internet", network: null,
  });
  return {
    networks: [{ ...net, state: "good", status: "active", devices: count, online: count,
                 sensor_ok: true, is_primary: true }],
    nodes: [internet, gateway, ...leaves],
    clusters: [{ id: "net-a", label: "Office", network_id: "net-a",
                 members: leaves.map((l) => l.id), health: "good" }],
    links: leaves.map((l) => ({ source: "gw", target: l.id, kind: "lan", pulse: false })),
    gaps: [],
  } as unknown as TopologyPayload;
}

/** One tile's height, for "is this box taller than a single row". */
const TILE_ISH = 70;

/** Three kinds of device on one network, so the grouping has something to
 *  group. Each group is kept under BUNDLE_THRESHOLD so the quiet-run
 *  bundler leaves them alone WITHOUT having to mark them risky — marking
 *  them risky is what made the first version of this fixture report every
 *  square as "alert". */
function mixed(over: { badPhone?: boolean } = {}): TopologyPayload {
  const mk = (i: number, group: string, state = "online") =>
    node(i, { group, risk: 0, state, id: `d${group}${i}` });
  const leaves = [
    ...Array.from({ length: 3 }, (_, i) => mk(i, "Phones", over.badPhone && i === 0 ? "offline" : "online")),
    ...Array.from({ length: 2 }, (_, i) => mk(i + 10, "Computers")),
    ...Array.from({ length: 2 }, (_, i) => mk(i + 20, "Cameras")),
  ];
  const base = payload(0);
  return {
    ...base,
    nodes: [...base.nodes, ...leaves],
    clusters: [{ id: "net-a", label: "Office", network_id: "net-a",
                 members: leaves.map((l) => l.id), health: "good" }],
    links: leaves.map((l) => ({ source: "gw", target: l.id, kind: "lan", pulse: false })),
  } as unknown as TopologyPayload;
}

describe("topology layout", () => {
  it("wraps a big segment instead of drawing one endless row", () => {
    const wide = computeLayout(payload(24), [], new Set());
    const rows = new Set(wide.items.filter((i) => i.row === "leaf").map((i) => i.y));
    expect(rows.size).toBeGreaterThan(1);
  });

  it("keeps the graph near enough to square to be fitted and still read", () => {
    // The number that mattered: 19 devices used to come out at 5.2:1, which
    // is why every tile was a smudge once the whole thing was on screen.
    const l = computeLayout(payload(19), [], new Set());
    expect(l.width / l.height).toBeLessThan(4);
  });

  it("squares devices up by what they ARE, one box per group", () => {
    // Miro's ask: a flat row of separate tiles is a list you read one
    // label at a time. Grouped, you take it in at a glance.
    const l = computeLayout(mixed(), [], new Set());
    const labels = l.groups.map((g) => g.label).sort();
    expect(labels).toEqual(["Cameras", "Computers", "Phones"]);
    expect(l.groups.find((g) => g.label === "Phones")?.count).toBe(3);
    // Biggest first: the eye lands on what the network is mostly made of.
    expect(l.groups[0].count).toBeGreaterThanOrEqual(l.groups[1].count);
  });

  it("a square is squarish — ten devices are 4x3, not another long strip", () => {
    const l = computeLayout(payload(10), [], new Set());
    const box = l.groups[0];
    const cols = new Set(l.items.filter((i) => i.row === "leaf").map((i) => i.x)).size;
    expect(cols).toBeLessThanOrEqual(4);
    expect(box.h).toBeGreaterThan(TILE_ISH);
  });

  it("every tile sits inside its own group's box", () => {
    // A tile drawn outside the square that claims it is the bug this
    // layout exists to avoid: the grouping would be decoration.
    const l = computeLayout(mixed(), [], new Set());
    const boxes = new Map(l.groups.map((g) => [g.id, g]));
    for (const it of l.items.filter((i) => i.row === "leaf")) {
      const g = boxes.get(it.groupId ?? "");
      expect(g, `tile ${it.x},${it.y} has no group`).toBeTruthy();
      expect(it.x).toBeGreaterThanOrEqual(g!.x);
      expect(it.y).toBeGreaterThanOrEqual(g!.y);
      expect(it.x + it.w).toBeLessThanOrEqual(g!.x + g!.w);
      expect(it.y + it.h).toBeLessThanOrEqual(g!.y + g!.h);
    }
  });

  it("a square with something wrong in it says so without being opened", () => {
    const l = computeLayout(mixed({ badPhone: true }), [], new Set());
    const phones = l.groups.find((g) => g.label === "Phones");
    expect(phones?.tone).toBe("alert");
    expect(l.groups.find((g) => g.label === "Computers")?.tone).not.toBe("alert");
  });

  it("the boundary box covers every row it holds", () => {
    const l = computeLayout(payload(24), [], new Set());
    const box = l.boundaries[0];
    const lowest = Math.max(...l.items.filter((i) => i.row === "leaf").map((i) => i.y + i.h));
    expect(box.y + box.h).toBeGreaterThanOrEqual(lowest);
  });

  it("reports a height that actually contains the graph", () => {
    // `height` used to be the constant `ROW_Y.leaf + TILE_H + pad`, which
    // stopped being true the moment a second row existed. A viewBox built
    // from a height that does not contain the content crops it silently.
    const l = computeLayout(payload(24), [], new Set());
    const lowest = Math.max(...l.items.map((i) => i.y + i.h));
    expect(l.height).toBeGreaterThanOrEqual(lowest);
  });

  it("is deterministic: the same payload lands in the same place twice", () => {
    const a = computeLayout(payload(24), [], new Set());
    const b = computeLayout(payload(24), [], new Set());
    expect(a.items.map((i) => [i.x, i.y])).toEqual(b.items.map((i) => [i.x, i.y]));
  });
});
