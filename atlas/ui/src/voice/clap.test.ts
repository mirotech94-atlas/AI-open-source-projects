import { describe, expect, it } from "vitest";
import { ClapGate, rms } from "./clap";

/** A block of `n` samples held at a constant amplitude -- its RMS is exactly
 *  `amplitude`, so it drives `push()` (the raw-audio entry point) with a
 *  perfectly predictable reading, the same way `handWith` gives `pinch.ts`'s
 *  tests an exact distance instead of a real hand. */
function frame(amplitude: number, n = 32): Float32Array {
  return new Float32Array(n).fill(amplitude);
}

/** Feed `n` quiet frames, `stepMs` apart, so the gate's rolling floor settles
 *  near `quiet` before a test's real action starts. */
function warm(gate: ClapGate, quiet: number, fromMs: number, stepMs = 10, n = 40): number {
  let at = fromMs;
  for (let i = 0; i < n; i += 1) {
    gate.pushEnergy(quiet, at);
    at += stepMs;
  }
  return at;
}

describe("rms", () => {
  it("is the amplitude of a constant block", () => {
    expect(rms(frame(0.5))).toBeCloseTo(0.5, 6);
  });

  it("is zero for silence and for an empty block", () => {
    expect(rms(frame(0))).toBe(0);
    expect(rms([])).toBe(0);
  });
});

describe("ClapGate", () => {
  it("fires on a double clap -- two onsets that each decay in time, spaced "
    + "150-600ms apart", () => {
    const gate = new ClapGate();
    let at = warm(gate, 0.01, 0);

    // First clap: a sharp rise, then straight back down within decayMs.
    expect(gate.pushEnergy(0.5, at)).toBe(false);       // onset
    at += 10;
    expect(gate.pushEnergy(0.01, at)).toBe(false);      // decays -- one half

    // Second clap, 300ms later -- inside the 150-600ms window.
    at += 300;
    expect(gate.pushEnergy(0.5, at)).toBe(false);       // onset
    at += 10;
    expect(gate.pushEnergy(0.01, at)).toBe(true);       // decays -- the pair fires
  });

  it("does the same thing fed raw audio frames through push()", () => {
    const gate = new ClapGate();
    let at = warm(gate, 0.01, 0);

    expect(gate.push(frame(0.5), at)).toBe(false);
    at += 10;
    expect(gate.push(frame(0.01), at)).toBe(false);

    at += 300;
    expect(gate.push(frame(0.5), at)).toBe(false);
    at += 10;
    expect(gate.push(frame(0.01), at)).toBe(true);
  });

  it("does not fire on a single clap, however long it keeps listening after", () => {
    const gate = new ClapGate();
    let at = warm(gate, 0.01, 0);

    expect(gate.pushEnergy(0.5, at)).toBe(false);
    at += 10;
    expect(gate.pushEnergy(0.01, at)).toBe(false);

    // A long, otherwise-quiet tail -- nothing here should ever fire, because
    // there is no second onset to pair the first one with.
    at = warm(gate, 0.01, at, 20, 100);
    expect(at).toBeGreaterThan(2000);
  });

  it("does not fire on speech-shaped energy -- a rise that does not decay "
    + "within decayMs never counts as an onset at all", () => {
    const gate = new ClapGate({ decayMs: 60 });
    let at = warm(gate, 0.01, 0);

    // Rises once and STAYS up for 300ms -- a word, not a transient.
    let fired = false;
    for (let i = 0; i < 30; i += 1) {
      if (gate.pushEnergy(0.5, at)) fired = true;
      at += 10;
    }
    expect(fired).toBe(false);

    // Drops back to quiet, then speaks again the same way. Two "words" must
    // not add up to a double clap either.
    at = warm(gate, 0.01, at, 10, 10);
    for (let i = 0; i < 30; i += 1) {
      if (gate.pushEnergy(0.5, at)) fired = true;
      at += 10;
    }
    expect(fired).toBe(false);
  });

  it("does not fire on ordinary syllabic speech -- several short "
    + "words, each well past decayMs, separated by ordinary pauses", () => {
    const gate = new ClapGate();
    let at = warm(gate, 0.01, 0);
    let fired = false;

    // Three "syllables": 100ms of speech-level energy (well past the 60ms
    // decay window, so none of them is ever confirmed as an onset), each
    // followed by a 40ms pause -- close enough together that the floor's
    // trackFloor() call inside the sustained-above branch (the ambient-
    // noise fix) must not accidentally re-arm the gate mid-utterance.
    for (let syllable = 0; syllable < 3; syllable += 1) {
      for (let i = 0; i < 10; i += 1) {
        if (gate.pushEnergy(0.5, at)) fired = true;
        at += 10;
      }
      for (let i = 0; i < 4; i += 1) {
        if (gate.pushEnergy(0.05, at)) fired = true;
        at += 10;
      }
    }
    expect(fired).toBe(false);

    // A real pause, then a real double clap -- the gate must still work
    // after all that speech, not just avoid firing during it.
    at = warm(gate, 0.01, at + 250, 10, 20);
    gate.pushEnergy(0.5, at); at += 10;
    gate.pushEnergy(0.01, at);
    at += 300;
    gate.pushEnergy(0.5, at); at += 10;
    expect(gate.pushEnergy(0.01, at)).toBe(true);
  });

  it("refuses to fire again during the refractory period after a fire, "
    + "then fires again once it has passed", () => {
    const gate = new ClapGate({ refractoryMs: 1000 });
    let at = warm(gate, 0.01, 0);

    // A confirmed double clap.
    gate.pushEnergy(0.5, at); at += 10;
    gate.pushEnergy(0.01, at);
    at += 300;
    gate.pushEnergy(0.5, at); at += 10;
    expect(gate.pushEnergy(0.01, at)).toBe(true);
    const firedAt = at;

    at = warm(gate, 0.01, at + 20, 10, 5);

    // Two more onsets, well-formed and well-spaced, but still inside the
    // refractory window -- neither may fire, and neither may be banked as
    // half of a pair either.
    gate.pushEnergy(0.5, at); at += 10;
    expect(gate.pushEnergy(0.01, at)).toBe(false);
    at += 300;
    gate.pushEnergy(0.5, at); at += 10;
    expect(gate.pushEnergy(0.01, at)).toBe(false);
    expect(at).toBeLessThan(firedAt + 1000);

    // Once the refractory period has fully elapsed, a fresh pair fires.
    at = firedAt + 1000 + 50;
    at = warm(gate, 0.01, at, 10, 5);
    gate.pushEnergy(0.5, at); at += 10;
    gate.pushEnergy(0.01, at);
    at += 300;
    gate.pushEnergy(0.5, at); at += 10;
    expect(gate.pushEnergy(0.01, at)).toBe(true);
  });

  it("never fires on two onsets closer together than minGapMs -- a clap "
    + "and its own echo, not a double clap", () => {
    const gate = new ClapGate();
    let at = warm(gate, 0.01, 0);

    gate.pushEnergy(0.5, at); at += 10;
    gate.pushEnergy(0.01, at);
    at += 50; // well under the 150ms floor
    gate.pushEnergy(0.5, at); at += 10;
    expect(gate.pushEnergy(0.01, at)).toBe(false);

    at = warm(gate, 0.01, at, 20, 50);
    expect(at).toBeGreaterThan(1000);
  });

  it("keeps hearing a clap after the room gets noisier -- a sustained rise "
    + "(a fan, music) must not permanently deafen the gate", () => {
    const gate = new ClapGate();
    let at = warm(gate, 0.01, 0);

    // The room gets noisier and stays there -- not a clap (it never decays),
    // so no onset is ever confirmed, but the floor must keep adapting or
    // every later frame is judged against a floor that is now hopelessly
    // out of date.
    for (let i = 0; i < 100; i += 1) {
      expect(gate.pushEnergy(0.1, at)).toBe(false);
      at += 10;
    }

    // A real double clap riding on top of the new, noisier baseline.
    gate.pushEnergy(0.8, at); at += 10;
    gate.pushEnergy(0.1, at);
    at += 300;
    gate.pushEnergy(0.8, at); at += 10;
    expect(gate.pushEnergy(0.1, at)).toBe(true);
  });

  it("does not pair two onsets spaced further apart than maxGapMs -- the "
    + "later one starts a fresh wait instead of being dropped entirely", () => {
    const gate = new ClapGate();
    let at = warm(gate, 0.01, 0);

    // Onset A.
    gate.pushEnergy(0.5, at); at += 10;
    gate.pushEnergy(0.01, at);

    // Onset B, 700ms later -- too slow to pair with A.
    at += 700;
    gate.pushEnergy(0.5, at); at += 10;
    expect(gate.pushEnergy(0.01, at)).toBe(false);

    // Onset C, 300ms after B -- pairs with B, not with the abandoned A.
    at += 300;
    gate.pushEnergy(0.5, at); at += 10;
    expect(gate.pushEnergy(0.01, at)).toBe(true);
  });
});
