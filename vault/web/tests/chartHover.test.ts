import { describe, expect, it } from "vitest";
import { sampleAt } from "../src/design/chartHover";

describe("sampleAt", () => {
  it("reports every series at one shared instant even when their timestamps are offset", () => {
    // Series A samples on the minute, series B is offset by 20s — a real-world
    // case where two sensors don't tick in lockstep.
    const a = [
      { t: 0, v: 10 },
      { t: 60_000, v: 20 },
      { t: 120_000, v: 30 },
    ];
    const b = [
      { t: 20_000, v: 100 },
      { t: 80_000, v: 200 },
      { t: 140_000, v: 300 },
    ];

    const sample = sampleAt([a, b], 65_000);
    expect(sample).not.toBeNull();

    // Both rows must be read at the exact same `t` — the whole point of the fix.
    const [rowA, rowB] = sample!.rows;
    expect(rowA.index).toBe(0);
    expect(rowB.index).toBe(1);
    expect(sample!.t).toBe(sample!.t);
    // The shared instant is the nearest timestamp across the UNION of both
    // series to the cursor (65_000): that's a's 60_000 (dist 5000) vs b's
    // 80_000 (dist 15000) — so 60_000 wins.
    expect(sample!.t).toBe(60_000);
    expect(rowA.value).toBe(20);
    // b has no point exactly at t=60_000 either — its nearest is 80_000, a
    // 20_000ms reach, which is within half its own median spacing (30_000ms),
    // so it still reports a value, but crucially at the SAME shared t=60_000
    // as row A, not at b's own nearest-to-cursor point.
    expect(rowB.value).toBe(200);
  });

  it("reads both series at the same instant when they share a nearby point", () => {
    const a = [
      { t: 0, v: 1 },
      { t: 10_000, v: 2 },
      { t: 20_000, v: 3 },
    ];
    const b = [
      { t: 0, v: 10 },
      { t: 10_000, v: 20 },
      { t: 20_000, v: 30 },
    ];

    const sample = sampleAt([a, b], 9_500);
    expect(sample).not.toBeNull();
    expect(sample!.t).toBe(10_000);
    expect(sample!.rows.map((r) => r.value)).toEqual([2, 20]);
  });

  it("returns null when there is no data at all", () => {
    expect(sampleAt([[], []], 100)).toBeNull();
  });

  it("reports a series' value as null at the shared instant when its own data is too sparse to cover it", () => {
    const dense = [
      { t: 0, v: 1 },
      { t: 1_000, v: 2 },
    ];
    const sparse = [
      { t: 10_000, v: 100 },
      { t: 20_000, v: 200 },
    ];

    // Cursor sits right on dense's first point, so the shared instant is t=0
    // for BOTH series — sparse's nearest (10_000) is 10s away, which exceeds
    // half its own 10_000ms spacing, so it must report null rather than
    // silently pairing dense's t=0 with a reading measured ten seconds later.
    const sample = sampleAt([dense, sparse], 0);
    expect(sample).not.toBeNull();
    expect(sample!.t).toBe(0);
    expect(sample!.rows.map((r) => r.value)).toEqual([1, null]);
  });

  it("reports a lone series' own value as null when nothing is within its spacing", () => {
    const a = [{ t: 0, v: 5 }];
    const sample = sampleAt([a], 0);
    expect(sample).not.toBeNull();
    expect(sample!.rows[0].value).toBe(5);
  });
});
