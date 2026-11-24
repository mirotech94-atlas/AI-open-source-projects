import { describe, expect, it } from "vitest";
import { BARGE_IN_HOLD_MS, initialBargeIn, stepBargeIn, type BargeInState } from "./bargeIn";

/**
 * Barge-in against what the analysers actually read, not round numbers.
 *
 * SPEAKER is ATLAS's own output as `readSpeaker` + `peakOf` see it: a
 * spoken sentence (macOS `say`, loudness-normalised to -16 LUFS, the level
 * a TTS provider ships), cut into 16 ms frames, each frame the peak of 38
 * three-sample RMS bars through the same (rms x 3.2)^0.7 shaping. It sits
 * near full scale -- median ~0.8, often 1 -- because the analyser reads the
 * digital signal before the system volume, whatever the speakers are at.
 *
 * The mic hears (a) the room's idle noise, (b) ATLAS leaking back through
 * the canceller -- `leak` x SPEAKER, 64 ms late, with a short room tail --
 * and (c) Miro, when he talks: the module's own calibration puts ordinary
 * speech near 0.28 on this scale, syllables and all.
 */
const SPEAKER = [
  1, 1, 1, .99, .96, .82, .84, .86, .91, 1, .51, 1, 1, 1, 1, .33, .41, .20, 0, 0, 0, .08, 0,
  0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, .08, .46, .89, .91, 1, 1, .95, 1, .94, 1, .79,
  .69, .59, .56, .41, .72, 1, .67, 1, .88, 1, .71, .73, .74, .89, 1, .67, .74, .75, .59, .46,
  .18, .08, .11, .24, .62, .87, 1, 1, 1, .79, 1, 1, 1, 1, 1, 1, 1, .77, .63, 1, 1, 1, 1, 1, 1,
  1, .48, .32, .27, .23, .18, .28, .27, 1, .84, .75, .76, .72, .46, .29, .15, .11, 0, .20, .42,
  .58, 1, 1, 1, .84, .91, 1, 1, 1, .94, 1, .82, .86, 1, 1, .78, 1, 1, .79, 1, .78, 1, 1, .96,
  .56, 1, 1, .78, .87, 1, 1, .41, .20, .12, .08, 0, 0, .08, .38, .89, 1, 1, 1, .66, .79, 1,
  .59, .51, .34, .59, .89, 1, 1, 1, 1, .74, .86, .59, .76, .62, .49, .34, .71, .76, .75, 1,
  .87, .71, .78, .57, .54, .67, 1, .75, 1, 1, .84, .92, 1, .93, .69, 1, .85, 1, .73, 1, .73,
  .78, .43, .30, .16, .40, .67, 1, 1, 1, 1, .89, .79, 1, 1, .47, .32, .37, .53, .58, .95, .57,
  .47, .40, .88, .87, .77, .81, 1, .64, 1, 1, .89, .58, .59, .20, .12, .39, .53, .70, .55,
  .22, .11, .24, .27, .85, .66, 1, .68, .62,
];
const FRAME = 16;
const FLOOR = 0.04;          // the quietest the last listening turn found
const IDLE = 0.055;          // the room between words, on this scale
const DELAY = 4;             // frames: output buffer + air + canceller

/** Deterministic jitter, so a run is the same run every time. */
function jitter(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

const speakerAt = (i: number): number => SPEAKER[i % SPEAKER.length] ?? 0;

/** Miro at an ordinary voice: syllables around `level`, short dips between. */
function miro(t: number, rand: () => number, level = 0.28): number {
  const inSyllable = t % 190 < 150;
  return inSyllable ? level * (0.85 + 0.3 * rand()) : level * 0.45;
}

interface Scene {
  /** How much of ATLAS the mic hears, frame by frame. */
  leak: number | ((t: number) => number);
  ms: number;
  /** His level at `t` into the answer, or null while he is quiet. */
  talks?: (t: number) => number | null;
  seed?: number;
  speaker?: (i: number) => number;
}

function play(scene: Scene, start: BargeInState = initialBargeIn()):
  { fired: number[]; state: BargeInState } {
  const rand = jitter(scene.seed ?? 7);
  const out = scene.speaker ?? speakerAt;
  const leakAt = typeof scene.leak === "number" ? (): number => scene.leak as number
    : scene.leak;
  let state = start;
  let tail = 0;
  const fired: number[] = [];
  for (let i = 0, t = 0; t < scene.ms; i += 1, t += FRAME) {
    const heard = out(Math.max(0, i - DELAY)) * leakAt(t);
    tail = Math.max(heard, tail * Math.exp(-FRAME / 120));
    const room = IDLE * (0.7 + 0.6 * rand());
    const voice = scene.talks?.(t) ?? null;
    const mic = Math.max(room, tail * (0.8 + 0.4 * rand()), voice ?? 0);
    const step = stepBargeIn(state, { now: 50_000 + t, mic, speaker: out(i), floor: FLOOR });
    state = step.state;
    if (step.interrupt) fired.push(t);
  }
  return { fired, state };
}

describe("barge-in at real playback levels", () => {
  it("an ordinary voice over a working canceller stops ATLAS within the hold", () => {
    // The bug: ATLAS's analyser sits near 1, so `echo x 0.3 + 0.06` put
    // the bar at ~0.33-0.36 -- above ordinary speech -- whatever the
    // canceller did. He could never talk over it at a normal voice.
    const from = 1200;
    const rand = jitter(3);
    const { fired } = play({
      leak: 0.08, ms: 3000,
      talks: (t) => (t >= from ? miro(t, rand) : null),
    });
    expect(fired).toHaveLength(1);
    expect(fired[0]! - from).toBeGreaterThanOrEqual(BARGE_IN_HOLD_MS);
    expect(fired[0]! - from).toBeLessThan(BARGE_IN_HOLD_MS + 120);
  });

  it("a quieter voice (0.22) still gets through a working canceller", () => {
    const from = 900;
    const rand = jitter(5);
    const { fired } = play({
      leak: 0.06, ms: 2500,
      talks: (t) => (t >= from ? miro(t, rand, 0.22) : null),
    });
    expect(fired).toHaveLength(1);
  });

  for (const leak of [0.08, 0.2, 0.35, 0.5, 0.7, 0.9, 1.0]) {
    it(`ATLAS never interrupts itself (leak ${leak}, a whole answer, three seeds)`, () => {
      for (const seed of [1, 2, 3]) {
        const { fired } = play({ leak, ms: 12_000, seed });
        expect(fired).toEqual([]);
      }
    });
  }

  it("a canceller still converging when the answer starts does not fire", () => {
    // A loud leak for the first second, then the canceller catches up.
    const { fired } = play({ leak: (t) => (t < 1000 ? 0.6 : 0.08), ms: 6000 });
    expect(fired).toEqual([]);
  });

  it("once the canceller has caught up, the bar comes back down and he gets through", () => {
    const rand = jitter(17);
    const { fired } = play({
      leak: (t) => (t < 1000 ? 0.6 : 0.08), ms: 6000,
      talks: (t) => (t >= 4000 ? miro(t, rand) : null),
    });
    expect(fired).toHaveLength(1);
    expect(fired[0]!).toBeGreaterThanOrEqual(4000 + BARGE_IN_HOLD_MS);
  });

  it("talking from the first word never fires early, and works after a pause", () => {
    // He talks through the measuring window, so the coupling reads high
    // (the safe failure). A pause lets the bar come back down.
    const rand = jitter(19);
    const talks = (t: number): number | null =>
      t < 1400 || t >= 3600 ? miro(t, rand) : null;
    const { fired } = play({ leak: 0.08, ms: 5500, talks });
    expect(fired).toHaveLength(1);
    expect(fired[0]!).toBeGreaterThanOrEqual(3600);
  });

  it("the echo tail when ATLAS stops mid-word never fires, even with a strong leak", () => {
    const words = (i: number): number => (i < 90 ? speakerAt(i) : 0);
    const { fired } = play({ leak: 0.7, ms: 3000, speaker: words });
    expect(fired).toEqual([]);
  });
});
