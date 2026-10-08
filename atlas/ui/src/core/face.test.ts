import { describe, expect, it } from "vitest";
import { faceState, isBlinking } from "./face";

const BASE = { level: 0, tMs: 0, seed: 20260924, reducedMotion: false, gaze: null };

describe("faceState — mouth follows the speaker level", () => {
  it("stays closed while nothing is being said", () => {
    const s = faceState({ ...BASE, phase: "dormant" });
    expect(s.mouthOpen).toBeCloseTo(0, 5);
  });

  it("opens more as the speaker level rises, while responding", () => {
    const quiet = faceState({ ...BASE, phase: "responding", level: 0.1 });
    const mid = faceState({ ...BASE, phase: "responding", level: 0.5 });
    const loud = faceState({ ...BASE, phase: "responding", level: 0.95 });
    expect(quiet.mouthOpen).toBeLessThan(mid.mouthOpen);
    expect(mid.mouthOpen).toBeLessThan(loud.mouthOpen);
    expect(loud.mouthOpen).toBeGreaterThan(0);
  });

  it("ignores level outside of responding — a loud room is not ATLAS talking", () => {
    const s = faceState({ ...BASE, phase: "listening", level: 0.9 });
    expect(s.mouthOpen).toBeCloseTo(0, 5);
  });

  it("clamps to at most fully open", () => {
    const s = faceState({ ...BASE, phase: "responding", level: 5 });
    expect(s.mouthOpen).toBeLessThanOrEqual(1);
  });

  it("rests in a slight smile when idle, mouth otherwise closed", () => {
    const s = faceState({ ...BASE, phase: "dormant" });
    expect(s.smile).toBeGreaterThan(0);
    expect(s.mouthOpen).toBeCloseTo(0, 5);
  });
});

describe("faceState — phase-driven eyes", () => {
  it("narrows through dormant < thinking < waking < listening", () => {
    const dormant = faceState({ ...BASE, phase: "dormant" });
    const thinking = faceState({ ...BASE, phase: "thinking" });
    const waking = faceState({ ...BASE, phase: "waking" });
    const listening = faceState({ ...BASE, phase: "listening" });
    expect(dormant.eyeOpen).toBeLessThan(thinking.eyeOpen);
    expect(thinking.eyeOpen).toBeLessThan(waking.eyeOpen);
    expect(waking.eyeOpen).toBeLessThan(listening.eyeOpen);
  });

  it("is half-closed at dormant, not shut", () => {
    const s = faceState({ ...BASE, phase: "dormant" });
    expect(s.eyeOpen).toBeGreaterThan(0.1);
    expect(s.eyeOpen).toBeLessThan(0.6);
  });

  it("tilts the head only while listening", () => {
    for (const phase of ["dormant", "waking", "thinking", "responding", "dismissing"] as const) {
      expect(faceState({ ...BASE, phase }).tiltDeg).toBe(0);
    }
    expect(faceState({ ...BASE, phase: "listening" }).tiltDeg).not.toBe(0);
  });

  it("only orbits particles while thinking", () => {
    for (const phase of ["dormant", "waking", "listening", "responding", "dismissing"] as const) {
      expect(faceState({ ...BASE, phase, tMs: 1234 }).orbit).toBeNull();
    }
    expect(faceState({ ...BASE, phase: "thinking", tMs: 1234 }).orbit).not.toBeNull();
  });

  it("breathes only while dormant", () => {
    const a = faceState({ ...BASE, phase: "dormant", tMs: 0 });
    const b = faceState({ ...BASE, phase: "dormant", tMs: 900 });
    expect(a.breathY).not.toBeCloseTo(b.breathY, 5);
    for (const phase of ["waking", "listening", "thinking", "responding", "dismissing"] as const) {
      expect(faceState({ ...BASE, phase, tMs: 900 }).breathY).toBe(0);
    }
  });
});

describe("faceState — gaze toward the cursor/hand", () => {
  it("looks straight ahead when nothing is known", () => {
    const s = faceState({ ...BASE, phase: "listening", gaze: null });
    expect(s.gazeX).toBe(0);
    expect(s.gazeY).toBe(0);
  });

  it("leans toward a known point, clamped to a small offset", () => {
    const right = faceState({ ...BASE, phase: "listening", gaze: { x: 1, y: 0.5 } });
    const left = faceState({ ...BASE, phase: "listening", gaze: { x: 0, y: 0.5 } });
    expect(right.gazeX).toBeGreaterThan(0);
    expect(left.gazeX).toBeLessThan(0);
    expect(Math.abs(right.gazeX)).toBeLessThanOrEqual(1);
  });
});

describe("blink cadence — deterministic under a seeded clock", () => {
  it("gives the same schedule for the same seed", () => {
    const times = [0, 400, 900, 1600, 2400, 3100, 3900, 4700, 5600, 7000];
    const a = times.map((t) => isBlinking(t, 42));
    const b = times.map((t) => isBlinking(t, 42));
    expect(b).toEqual(a);
  });

  it("moves the schedule when the seed changes", () => {
    const times = Array.from({ length: 40 }, (_, i) => i * 250);
    const a = times.map((t) => isBlinking(t, 1));
    const b = times.map((t) => isBlinking(t, 2));
    expect(b).not.toEqual(a);
  });

  it("actually blinks sometimes and not always", () => {
    const times = Array.from({ length: 200 }, (_, i) => i * 50);
    const hits = times.filter((t) => isBlinking(t, 7));
    expect(hits.length).toBeGreaterThan(0);
    expect(hits.length).toBeLessThan(times.length / 2);
  });
});

describe("faceState — prefers-reduced-motion", () => {
  it("never blinks", () => {
    const times = Array.from({ length: 400 }, (_, i) => i * 50);
    const anyBlink = times.some(
      (t) => faceState({ ...BASE, phase: "listening", tMs: t, reducedMotion: true }).blinking,
    );
    expect(anyBlink).toBe(false);
  });

  it("does not orbit or breathe", () => {
    const thinking = faceState({
      ...BASE, phase: "thinking", tMs: 2000, reducedMotion: true,
    });
    expect(thinking.orbit).toBeNull();
    const dormant = faceState({
      ...BASE, phase: "dormant", tMs: 2000, reducedMotion: true,
    });
    expect(dormant.breathY).toBe(0);
  });

  it("still moves the mouth with speaker level — that is a real measurement, not motion for its own sake", () => {
    const s = faceState({
      ...BASE, phase: "responding", level: 0.7, tMs: 2000, reducedMotion: true,
    });
    expect(s.mouthOpen).toBeGreaterThan(0);
  });
});
