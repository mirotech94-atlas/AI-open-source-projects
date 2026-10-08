import { describe, expect, it } from "vitest";
import {
  BUST_POINTS,
  PART,
  bustGeometry,
  faceCentre,
  headAnchor,
} from "./bust-geometry";

describe("bustGeometry", () => {
  it("returns three finite floats per point and one part per point", () => {
    const cloud = bustGeometry(7, 4000);
    expect(cloud.count).toBe(4000);
    expect(cloud.positions.length).toBe(4000 * 3);
    expect(cloud.parts.length).toBe(4000);
    expect(cloud.phases.length).toBe(4000);
    expect(Array.from(cloud.positions).every(Number.isFinite)).toBe(true);
  });

  it("is deterministic: the same seed gives byte-identical output", () => {
    const a = bustGeometry(11, 3000);
    const b = bustGeometry(11, 3000);
    expect(Array.from(b.positions)).toEqual(Array.from(a.positions));
    expect(Array.from(b.parts)).toEqual(Array.from(a.parts));
    expect(Array.from(b.phases)).toEqual(Array.from(a.phases));
  });

  it("is not a fixed shape: a different seed moves the points", () => {
    const a = bustGeometry(11, 3000);
    const b = bustGeometry(12, 3000);
    expect(Array.from(b.positions)).not.toEqual(Array.from(a.positions));
  });

  // The stage asks for ~18k points; a default that drifted from the brief
  // would be invisible until the frame rate changed.
  it("defaults to the figure the stage is built for", () => {
    expect(BUST_POINTS).toBe(18000);
    expect(bustGeometry().count).toBe(18000);
  });

  it("gives every part of the bust some points", () => {
    const cloud = bustGeometry(3);
    const tally = new Map<number, number>();
    for (const part of cloud.parts) tally.set(part, (tally.get(part) ?? 0) + 1);
    for (const part of [PART.HEAD, PART.FACE, PART.NECK, PART.SHOULDERS]) {
      expect(tally.get(part) ?? 0).toBeGreaterThan(0);
    }
    // Every point belongs to exactly one of the four.
    expect([...tally.keys()].sort()).toEqual([0, 1, 2, 3]);
  });

  /** The amber core is the one thing on this stage that has to be somewhere
   *  specific: in the face, in front of the head's centre. */
  it("puts the face cluster in front of the head, around the face centre", () => {
    const cloud = bustGeometry(5);
    let n = 0;
    let sumZ = 0;
    let worst = 0;
    for (let i = 0; i < cloud.count; i += 1) {
      if (cloud.parts[i] !== PART.FACE) continue;
      const x = cloud.positions[i * 3] ?? 0;
      const y = cloud.positions[i * 3 + 1] ?? 0;
      const z = cloud.positions[i * 3 + 2] ?? 0;
      n += 1;
      sumZ += z;
      worst = Math.max(worst, Math.hypot(
        x - faceCentre[0], y - faceCentre[1], z - faceCentre[2]));
    }
    expect(n).toBeGreaterThan(200);
    expect(sumZ / n).toBeGreaterThan(0);        // in front, not behind
    expect(worst).toBeLessThan(0.5);            // a cluster, not a haze
  });

  it("keeps the whole bust inside a sane bounding box", () => {
    const cloud = bustGeometry(9);
    let maxAbs = 0;
    for (const v of cloud.positions) maxAbs = Math.max(maxAbs, Math.abs(v));
    expect(maxAbs).toBeLessThan(3);
  });

  it("phases are usable as animation offsets: in [0, 2π)", () => {
    const cloud = bustGeometry(2, 2000);
    for (const phase of cloud.phases) {
      expect(phase).toBeGreaterThanOrEqual(0);
      expect(phase).toBeLessThan(Math.PI * 2);
    }
  });

  it("anchors the brain's edges at the head, above the shoulders", () => {
    expect(headAnchor[1]).toBeGreaterThan(0.5);
  });
});
