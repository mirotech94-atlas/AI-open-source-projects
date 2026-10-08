import { describe, expect, it } from "vitest";
// Only the pure parts: loadWhisperGate needs a browser Worker and the model.
import {
  GateHealth,
  HOP_SECONDS,
  MAX_FAILED_WINDOWS,
  TARGET_RATE,
  WINDOW_SECONDS,
  WakeWindow,
  containsWake,
  resample,
} from "./localWake";

describe("containsWake", () => {
  it.each(["hey atlas", "Atlas, you up?", "ATLAS.", "ok atlas what's new", "atlas"])(
    "hears the wake word in %j",
    (text) => {
      expect(containsWake(text)).toBe(true);
    },
  );

  it.each([
    "At last, the report",
    "at last",
    "atlases are heavy",
    "the atlassian ticket",
    "",
  ])("does not hear it in %j", (text) => {
    expect(containsWake(text)).toBe(false);
  });
});

describe("resample", () => {
  it("returns the same samples when the rate already matches", () => {
    const input = new Float32Array([0.1, 0.2, 0.3]);
    expect(Array.from(resample(input, TARGET_RATE))).toEqual(Array.from(input));
  });

  it("brings 48 kHz down to 16 kHz: a third of the samples", () => {
    const input = new Float32Array(48000).fill(0.5);
    const out = resample(input, 48000);
    expect(out.length).toBe(16000);
    expect(out[100]).toBeCloseTo(0.5, 5);
  });

  it("handles 44.1 kHz, which is not a whole multiple", () => {
    const input = new Float32Array(44100);
    expect(resample(input, 44100).length).toBe(16000);
  });
});

describe("WakeWindow", () => {
  it("is two seconds long with a one-second hop", () => {
    expect(WINDOW_SECONDS).toBe(2);
    expect(HOP_SECONDS).toBe(1);
  });

  it("yields nothing until a full window has arrived", () => {
    const w = new WakeWindow(1000);
    expect(w.push(new Float32Array(1500))).toBeNull();
    const first = w.push(new Float32Array(500));
    expect(first?.length).toBe(2000);
  });

  it("then yields again every hop, never keeping more than one window", () => {
    const w = new WakeWindow(1000);
    w.push(new Float32Array(2000).fill(1));
    expect(w.push(new Float32Array(999).fill(2))).toBeNull();
    const second = w.push(new Float32Array(1).fill(2));
    expect(second?.length).toBe(2000);
    // The last second of the old audio, then the new second.
    expect(second?.[0]).toBe(1);
    expect(second?.[1999]).toBe(2);
    expect(w.held).toBeLessThanOrEqual(2000);
  });

  it("clear() forgets everything, so one 'atlas' cannot wake him twice", () => {
    const w = new WakeWindow(1000);
    w.push(new Float32Array(2000));
    w.clear();
    expect(w.held).toBe(0);
    expect(w.push(new Float32Array(1000))).toBeNull();
  });
});

describe("GateHealth", () => {
  it("forgives an odd failed window", () => {
    const health = new GateHealth();
    expect(health.record(false)).toBeNull();
    expect(health.record(true)).toBeNull();
    expect(health.record(false)).toBeNull();
    expect(health.stopped).toBeNull();
  });

  it("stops after consecutive failures, and stays stopped", () => {
    const health = new GateHealth();
    let reason: string | null = null;
    for (let i = 0; i < MAX_FAILED_WINDOWS; i += 1) reason = health.record(false);
    expect(reason).toBe("the model could not read the audio");
    expect(health.record(true)).toBe("the model could not read the audio");
  });

  it("stops outright when the worker dies, keeping the first reason", () => {
    const health = new GateHealth();
    expect(health.stop("the worker crashed")).toBe("the worker crashed");
    expect(health.stop("closed")).toBe("the worker crashed");
    expect(health.stopped).toBe("the worker crashed");
  });
});
