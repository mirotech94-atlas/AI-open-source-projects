import { describe, expect, it } from "vitest";
import { splitFlanks, type FlankUnit } from "./flankLayout";

const u = (id: string, weight: number, order = 1, cluster?: string): FlankUnit =>
  ({ id, weight, order, cluster });

const UNITS: FlankUnit[] = [
  u("tile-a", 1, 0), u("tile-b", 1, 0), u("tile-c", 1, 0), u("tile-d", 1, 0), u("tile-e", 1, 0),
  u("roster", 6, 1, "agents"), u("shifts", 3, 3, "agents"),
  u("needs", 6, 1, "attention"), u("next", 1, 0, "attention"),
  u("vitals", 4, 2),
  u("leads", 4, 4, "pipeline"), u("pipeline", 3, 5, "pipeline"),
];

const ids = (l: FlankUnit[]): string[] => l.map((x) => x.id);

describe("splitFlanks", () => {
  it("puts every unit on exactly one side, none lost or doubled", () => {
    const { left, right } = splitFlanks(UNITS);
    expect([...ids(left), ...ids(right)].sort()).toEqual(UNITS.map((x) => x.id).sort());
  });

  it("is deterministic and ignores input order", () => {
    const a = splitFlanks(UNITS);
    const b = splitFlanks([...UNITS].reverse());
    expect(ids(b.left)).toEqual(ids(a.left));
    expect(ids(b.right)).toEqual(ids(a.right));
  });

  it("balances by weight: the sides differ by less than the heaviest cluster", () => {
    const { leftWeight, rightWeight } = splitFlanks(UNITS);
    expect(Math.abs(leftWeight - rightWeight)).toBeLessThanOrEqual(2);
    expect(leftWeight + rightWeight).toBe(UNITS.reduce((s, x) => s + x.weight, 0));
  });

  it("keeps a cluster together on one side", () => {
    const { left, right } = splitFlanks(UNITS);
    for (const group of [["roster", "shifts"], ["needs", "next"], ["leads", "pipeline"]]) {
      const inLeft = group.filter((g) => ids(left).includes(g)).length;
      expect([0, group.length]).toContain(inLeft);
      expect(group.filter((g) => ids(right).includes(g)).length).toBe(group.length - inLeft);
    }
  });

  it("splits the KPI tiles across both sides", () => {
    const { left, right } = splitFlanks(UNITS);
    const n = (l: FlankUnit[]): number => l.filter((x) => x.id.startsWith("tile-")).length;
    expect(n(left)).toBeGreaterThan(0);
    expect(n(right)).toBeGreaterThan(0);
  });

  it("orders each side by `order`, then by input position", () => {
    const { left, right } = splitFlanks(UNITS);
    for (const side of [left, right]) {
      const o = side.map((x) => x.order);
      expect(o).toEqual([...o].sort((p, q) => p - q));
    }
  });

  it("copes with no units and with one", () => {
    expect(splitFlanks([])).toMatchObject({ left: [], right: [] });
    const one = splitFlanks([u("only", 2)]);
    expect(ids(one.left)).toEqual(["only"]);
    expect(one.right).toEqual([]);
  });
});
