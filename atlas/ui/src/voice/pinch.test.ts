import { describe, expect, it } from "vitest";
import { INDEX_TIP, PinchGate, THUMB_TIP, isPinch, pinchDistance, type Point } from "./pinch";

/** A minimal 21-point hand, with thumb tip and index tip placed explicitly
 * and everything else parked at the origin -- pinchDistance only looks at
 * the two tips, so the rest of the points are irrelevant filler. */
function handWith(thumb: Point, index: Point): Point[] {
  const points: Point[] = new Array(21).fill(null).map(() => ({ x: 0, y: 0, z: 0 }));
  points[THUMB_TIP] = thumb;
  points[INDEX_TIP] = index;
  return points;
}

describe("pinchDistance", () => {
  it("measures the distance between thumb tip and index tip on a full hand", () => {
    const hand = handWith({ x: 0, y: 0 }, { x: 3, y: 4 });
    expect(pinchDistance(hand)).toBeCloseTo(5, 5);
  });

  it("is null when fewer than 9 points are given", () => {
    const short = new Array(8).fill(null).map(() => ({ x: 0, y: 0 }));
    expect(pinchDistance(short)).toBeNull();
  });

  it("ignores z -- MediaPipe's wrist-relative depth is noisier and on a "
    + "different scale than the normalised x/y plane, so folding it in "
    + "would only ever inflate the reading and make a real pinch flicker "
    + "shut", () => {
    const hand = handWith({ x: 0, y: 0, z: 5 }, { x: 3, y: 4, z: -5 });
    expect(pinchDistance(hand)).toBeCloseTo(5, 5);
  });
});

describe("isPinch", () => {
  it("is true when the tips are closer than the threshold", () => {
    const hand = handWith({ x: 0, y: 0 }, { x: 0.01, y: 0 });
    expect(isPinch(hand, 0.05)).toBe(true);
  });

  it("is false when the tips are farther than the threshold", () => {
    const hand = handWith({ x: 0, y: 0 }, { x: 0.2, y: 0 });
    expect(isPinch(hand, 0.05)).toBe(false);
  });

  it("is false on a hand with only 3 points", () => {
    const short = [{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 0, y: 0 }];
    expect(isPinch(short, 0.05)).toBe(false);
  });
});

describe("PinchGate", () => {
  it("fires once after N consecutive pinched frames, not on the first", () => {
    const gate = new PinchGate(3, 5);
    expect(gate.push(true)).toBe(false);
    expect(gate.push(true)).toBe(false);
    expect(gate.push(true)).toBe(true);
  });

  it("does not fire again until M consecutive open frames have released it", () => {
    const gate = new PinchGate(3, 5);
    gate.push(true);
    gate.push(true);
    expect(gate.push(true)).toBe(true);

    // Still pinched: already fired, must not fire again without a release.
    expect(gate.push(true)).toBe(false);
    expect(gate.push(true)).toBe(false);

    // Open, but not yet for M consecutive frames.
    expect(gate.push(false)).toBe(false);
    expect(gate.push(false)).toBe(false);
    expect(gate.push(false)).toBe(false);
    expect(gate.push(false)).toBe(false);
    // 4 open frames so far -- one short of the release threshold.
    expect(gate.push(true)).toBe(false); // re-pinching before release: no fire

    // Now truly release with 5 consecutive open frames.
    gate.push(false);
    gate.push(false);
    gate.push(false);
    gate.push(false);
    gate.push(false);

    // And it can fire again.
    expect(gate.push(true)).toBe(false);
    expect(gate.push(true)).toBe(false);
    expect(gate.push(true)).toBe(true);
  });
});
