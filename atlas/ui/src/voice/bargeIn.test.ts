import { describe, expect, it } from "vitest";
import {
  BARGE_IN_GAP_MS,
  BARGE_IN_HOLD_MS,
  BARGE_IN_MIN_LEVEL,
  WARMUP_MAX_MS,
  WARMUP_MS,
  bargeInThreshold,
  initialBargeIn,
  peakOf,
  stepBargeIn,
  type BargeInState,
} from "./bargeIn";

const FRAME = 16; // ~60 fps, what requestAnimationFrame gives the poll loop
const QUIET_ROOM = 0.05;
const START = 10_000;

type Frame = (t: number) => { mic: number; speaker: number | null };

/** Feed frames from `from` for `ms`; returns the state and every firing time
 *  (relative to `from`). */
function run(state: BargeInState, from: number, ms: number, frame: Frame):
  { state: BargeInState; fired: number[]; end: number } {
  const fired: number[] = [];
  let s = state;
  let t = from;
  for (; t < from + ms; t += FRAME) {
    const { mic, speaker } = frame(t - from);
    const step = stepBargeIn(s, { now: t, mic, speaker, floor: QUIET_ROOM });
    s = step.state;
    if (step.interrupt) fired.push(t - from);
  }
  return { state: s, fired, end: t };
}

/** The first moments of an answer, with Miro quiet: ATLAS at `speaker`, the
 *  mic hearing `leak` of it. */
function warmed(leak: number, speaker = 0.6): { state: BargeInState; end: number } {
  const warm = run(initialBargeIn(), START, WARMUP_MS + 4 * FRAME,
                   () => ({ mic: speaker * leak, speaker }));
  expect(warm.fired).toEqual([]);
  expect(warm.state.coupling).not.toBeNull();
  return warm;
}

describe("stepBargeIn", () => {
  it("never fires on ATLAS's own voice, even with no echo cancellation at all", () => {
    // The worst case: the mic hears ATLAS as loud as ATLAS is playing, for
    // a whole answer.
    const { fired } = run(initialBargeIn(), START, 5000, () => ({ mic: 0.6, speaker: 0.6 }));
    expect(fired).toEqual([]);
  });

  it("never fires on the echo tail after a word ends", () => {
    // A strong leak, then ATLAS stops a word: the room keeps ringing into
    // the mic after the analyser has already gone quiet.
    const warm = warmed(0.7);
    const tail = run(warm.state, warm.end, 600, (t) => ({
      mic: 0.42 * Math.exp(-t / 120),
      speaker: 0,
    }));
    expect(tail.fired).toEqual([]);
  });

  it("fires once when he keeps talking over ATLAS at a normal voice", () => {
    // The canceller working: the mic hears a tenth of ATLAS.
    const warm = warmed(0.1);
    const { fired } = run(warm.state, warm.end, 1000, () => ({ mic: 0.35, speaker: 0.6 }));
    expect(fired).toHaveLength(1);
    // Not before the hold, and not much after it.
    expect(fired[0]).toBeGreaterThanOrEqual(BARGE_IN_HOLD_MS);
    expect(fired[0]).toBeLessThan(BARGE_IN_HOLD_MS + 3 * FRAME);
  });

  it("with a strong leak, fires only when he is clearly louder than it", () => {
    const warm = warmed(0.5);
    const normal = run(warm.state, warm.end, 1000, () => ({ mic: 0.35, speaker: 0.4 }));
    expect(normal.fired).toEqual([]);
    const raised = run(warm.state, warm.end, 1000, () => ({ mic: 0.8, speaker: 0.4 }));
    expect(raised.fired).toHaveLength(1);
  });

  it("does not fire during the measuring window", () => {
    const { fired } = run(initialBargeIn(), START, WARMUP_MS, () => ({ mic: 0.9, speaker: 0.6 }));
    expect(fired).toEqual([]);
  });

  it("talking during the measuring window only makes interrupting harder", () => {
    // He is loud from the first frame: the coupling measures high, and the
    // safe failure is ATLAS finishing the sentence.
    const early = run(initialBargeIn(), START, 3000, () => ({ mic: 0.5, speaker: 0.6 }));
    expect(early.fired).toEqual([]);
    expect(early.state.coupling).not.toBeNull();
    expect(early.state.coupling!).toBeGreaterThan(0.5);
  });

  it("arms after the longest window even if ATLAS stayed quiet", () => {
    const quiet = run(initialBargeIn(), START, WARMUP_MAX_MS + FRAME,
                      () => ({ mic: 0.02, speaker: 0.05 }));
    expect(quiet.state.coupling).not.toBeNull();
    const talk = run(quiet.state, quiet.end, 800, () => ({ mic: 0.35, speaker: 0.05 }));
    expect(talk.fired).toHaveLength(1);
  });

  it("does not fire on a short burst (a cough, a door)", () => {
    const warm = warmed(0.1);
    const burst = BARGE_IN_HOLD_MS - 80;
    const { fired } = run(warm.state, warm.end, 1000, (t) => ({
      mic: t < burst ? 0.6 : 0.04,
      speaker: 0.3,
    }));
    expect(fired).toEqual([]);
  });

  it("forgives dips shorter than the gap allowance", () => {
    const warm = warmed(0.1);
    const dip = BARGE_IN_GAP_MS - 30;
    // Syllables of 120ms, each followed by a dip just inside the allowance.
    const { fired } = run(warm.state, warm.end, 800, (t) => ({
      mic: t % (120 + dip) < 120 ? 0.35 : 0.02,
      speaker: 0.2,
    }));
    expect(fired.length).toBeGreaterThanOrEqual(1);
  });

  it("restarts the hold after a real pause", () => {
    // Two 200ms bursts with a longer-than-gap pause never add up to one.
    const warm = warmed(0.1);
    const second = 200 + BARGE_IN_GAP_MS + 100;
    const { fired } = run(warm.state, warm.end, 1000, (t) => ({
      mic: t < 200 || (t >= second && t < second + 200) ? 0.4 : 0.02,
      speaker: 0.2,
    }));
    expect(fired).toEqual([]);
  });

  it("never fires without a speaker reading -- echo cannot be ruled out", () => {
    const { fired } = run(initialBargeIn(), START, 3000, () => ({ mic: 0.9, speaker: null }));
    expect(fired).toEqual([]);
    // And a warmed state going blind stops firing too.
    const warm = warmed(0.1);
    const blind = run(warm.state, warm.end, 1000, () => ({ mic: 0.9, speaker: null }));
    expect(blind.fired).toEqual([]);
  });

  it("ignores room noise below the absolute minimum", () => {
    const warm = warmed(0.1, 0.2);
    const { fired } = run(warm.state, warm.end, 2000, () => ({
      mic: BARGE_IN_MIN_LEVEL - 0.01,
      speaker: 0,
    }));
    expect(fired).toEqual([]);
  });
});

describe("bargeInThreshold", () => {
  it("rises with ATLAS's own level and with the measured coupling", () => {
    expect(bargeInThreshold(0.8, QUIET_ROOM, 0.2))
      .toBeGreaterThan(bargeInThreshold(0.2, QUIET_ROOM, 0.2));
    expect(bargeInThreshold(0.5, QUIET_ROOM, 0.9))
      .toBeGreaterThan(bargeInThreshold(0.5, QUIET_ROOM, 0.1));
  });

  it("is never below the absolute minimum", () => {
    expect(bargeInThreshold(0, 0, 0)).toBe(BARGE_IN_MIN_LEVEL);
  });

  it("stays reachable when the room's floor was never measured", () => {
    // The listening loop starts its floor at 1 and pulls it down; a turn
    // that never found its quiet must not put barge-in out of reach.
    expect(bargeInThreshold(0, 1, 0.1)).toBeLessThan(0.4);
  });
});

describe("peakOf", () => {
  it("is null when there is no reading", () => {
    expect(peakOf(null)).toBeNull();
  });

  it("is the loudest bar", () => {
    expect(peakOf([0.1, 0.5, 0.3])).toBe(0.5);
  });
});
