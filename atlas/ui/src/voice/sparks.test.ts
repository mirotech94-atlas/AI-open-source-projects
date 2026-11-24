import { describe, expect, it } from "vitest";
import {
  MAX_SPARKS, SILENT_LEVEL, SparkField, levelOf, spawnRate,
} from "./sparks";

/** A fixed "random", so a test is the same every time. */
function steady(value = 0.5): () => number {
  return () => value;
}

describe("levelOf", () => {
  it("reads silence (128 everywhere) as zero", () => {
    expect(levelOf(new Uint8Array(128).fill(128))).toBe(0);
  });

  it("reads a loud signal as close to one, and never above it", () => {
    const loud = new Uint8Array(128).map((_, i) => (i % 2 === 0 ? 255 : 0));
    const level = levelOf(loud);
    expect(level).toBeGreaterThan(0.9);
    expect(level).toBeLessThanOrEqual(1);
  });

  it("follows the amplitude: louder reads higher", () => {
    const at = (amp: number): number =>
      levelOf(new Uint8Array(128).map((_, i) => 128 + (i % 2 === 0 ? amp : -amp)));
    // Below the point where the shaping clips at 1.
    expect(at(3)).toBeLessThan(at(8));
    expect(at(8)).toBeLessThan(at(20));
  });

  it("is zero for an empty buffer", () => {
    expect(levelOf(new Uint8Array(0))).toBe(0);
  });
});

describe("spawnRate", () => {
  it("is zero in silence", () => {
    expect(spawnRate(0)).toBe(0);
    expect(spawnRate(SILENT_LEVEL * 0.9)).toBe(0);
  });

  it("rises with the level", () => {
    expect(spawnRate(0.2)).toBeGreaterThan(0);
    expect(spawnRate(0.8)).toBeGreaterThan(spawnRate(0.2));
  });
});

describe("SparkField", () => {
  const origin = { x: 100, y: 100, r: 40 };

  it("makes sparks while the voice is loud", () => {
    const field = new SparkField(steady());
    for (let i = 0; i < 20; i += 1) field.step(0.05, 0.8, origin);
    expect(field.sparks.length).toBeGreaterThan(5);
  });

  it("makes none, and lets the last ones die, when the voice stops", () => {
    const field = new SparkField(steady());
    for (let i = 0; i < 20; i += 1) field.step(0.05, 0.8, origin);
    const before = field.sparks.length;
    expect(before).toBeGreaterThan(0);
    for (let i = 0; i < 60; i += 1) field.step(0.05, 0, origin);   // 3 s of quiet
    expect(field.sparks.length).toBe(0);
  });

  it("starts nothing in silence", () => {
    const field = new SparkField(steady());
    for (let i = 0; i < 40; i += 1) field.step(0.05, 0, origin);
    expect(field.sparks.length).toBe(0);
  });

  it("makes more sparks for a louder voice", () => {
    const quiet = new SparkField(steady());
    const loud = new SparkField(steady());
    for (let i = 0; i < 20; i += 1) {
      quiet.step(0.05, 0.15, origin);
      loud.step(0.05, 0.9, origin);
    }
    expect(loud.sparks.length).toBeGreaterThan(quiet.sparks.length);
  });

  it("sparks rise: y goes up the screen (smaller) as they age", () => {
    const field = new SparkField(steady());
    field.step(0.05, 0.9, origin);
    const first = field.sparks[0];
    expect(first).toBeDefined();
    const y0 = first!.y;
    for (let i = 0; i < 6; i += 1) field.step(0.05, 0.9, origin);
    expect(first!.y).toBeLessThan(y0);
  });

  it("sparks drift sideways", () => {
    const field = new SparkField(steady(0.9));
    field.step(0.05, 0.9, origin);
    const first = field.sparks[0]!;
    const x0 = first.x;
    for (let i = 0; i < 8; i += 1) field.step(0.05, 0.9, origin);
    expect(first.x).not.toBe(x0);
  });

  it("never holds more than the cap", () => {
    const field = new SparkField(steady());
    for (let i = 0; i < 400; i += 1) field.step(0.05, 1, origin);
    expect(field.sparks.length).toBeLessThanOrEqual(MAX_SPARKS);
  });

  it("clear() empties the air at once", () => {
    const field = new SparkField(steady());
    for (let i = 0; i < 10; i += 1) field.step(0.05, 1, origin);
    field.clear();
    expect(field.sparks.length).toBe(0);
  });

  it("a stalled frame (a huge dt) does not burst out a flood", () => {
    const field = new SparkField(steady());
    field.step(5, 1, origin);
    expect(field.sparks.length).toBeLessThanOrEqual(MAX_SPARKS);
    expect(field.sparks.length).toBeLessThan(40);
  });
});
