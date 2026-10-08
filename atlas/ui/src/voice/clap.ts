/**
 * Clap-to-wake — pure signal rules, no DOM, no microphone.
 *
 * A clap is a short, loud transient: energy jumps well above the room's
 * quiet floor and falls back down again within about 60 ms. Speech does the
 * first half of that (energy rises above the floor) but not the second — a
 * word or a sentence stays elevated for hundreds of milliseconds, not tens.
 * That difference, not loudness alone, is what tells a clap apart from
 * someone talking near the microphone.
 *
 * One clap is easy to trigger by accident (a dropped object, a door). Two,
 * spaced the way an actual double-clap is spaced, are not. `ClapGate` fires
 * only on the second onset of a pair that both decayed in time and landed
 * `minGapMs`..`maxGapMs` apart, then refuses to fire again for
 * `refractoryMs` — the same "confirm, then debounce" shape `PinchGate` uses
 * for a pinch.
 *
 * This file never opens a stream. It is fed frames of audio (a
 * `Float32Array` of PCM samples) and a monotonic timestamp by whatever reads
 * the microphone in `detector.ts`, and it returns a plain boolean. Nothing
 * here can be wired into anything that uploads a frame.
 */

/** Root-mean-square energy of a block of PCM samples, in the same units as
 *  the samples themselves (typically -1..1). */
export function rms(samples: ArrayLike<number>): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const s = samples[i] ?? 0;
    sum += s * s;
  }
  return Math.sqrt(sum / samples.length);
}

export interface ClapGateOptions {
  /** An onset is a frame whose RMS is at least this many times the rolling
   *  quiet floor. */
  onsetMultiplier?: number;
  /** How fast the quiet floor tracks the room while nothing is happening
   *  (0..1 -- higher adapts faster). */
  floorAttack?: number;
  /** A floor of exactly zero would make anything above it "infinitely"
   *  loud; this is the smallest floor the gate will track down to. */
  minFloor?: number;
  /** An onset must decay back under the threshold within this long to count
   *  as a clap rather than as sustained, speech-shaped energy. */
  decayMs?: number;
  /** Two confirmed onsets closer together than this are one clap and its
   *  own echo, not a double clap. */
  minGapMs?: number;
  /** Two confirmed onsets further apart than this are not a pair; the later
   *  one starts a fresh wait instead. */
  maxGapMs?: number;
  /** After a fire, no onset counts towards a new pair until this much time
   *  has passed -- a held or repeated clap cannot spam wakes. */
  refractoryMs?: number;
}

const DEFAULTS: Required<ClapGateOptions> = {
  onsetMultiplier: 5,
  floorAttack: 0.2,
  minFloor: 1e-4,
  decayMs: 60,
  minGapMs: 150,
  maxGapMs: 600,
  refractoryMs: 1000,
};

interface PendingOnset {
  /** When the rising edge was seen. */
  startedAt: number;
  /** By when it must have decayed to count as a clap. */
  deadline: number;
}

/**
 * Turns a stream of (energy, time) frames into a single "double clap" wake
 * edge. `push` is the only thing that matters here: everything else is
 * private bookkeeping.
 */
export class ClapGate {
  private readonly opts: Required<ClapGateOptions>;

  private floor: number;
  /** The floor starts out unknown, not zero. Comparing a real first frame
   *  against a floor of (near) zero would read that frame itself as an
   *  onset -- the room's own quiet hum "jumping" above a floor that was
   *  never allowed to learn what quiet sounds like. The first frame this
   *  gate ever sees instead seeds the floor directly and is never itself
   *  treated as an onset. */
  private seeded = false;
  /** True from a confirmed rising edge until the energy is back down,
   *  whether or not that drop happened in time to count as a clap -- this
   *  is what stops sustained speech being read as one onset per frame. */
  private above = false;
  private pending: PendingOnset | null = null;
  /** A confirmed onset waiting for a second one to pair with. */
  private waitingOnset: number | null = null;
  private refractoryUntil = 0;

  constructor(options: ClapGateOptions = {}) {
    this.opts = { ...DEFAULTS, ...options };
    this.floor = this.opts.minFloor;
  }

  /**
   * Feed one frame. `samples` may be raw PCM (its RMS is measured here) or,
   * for callers that already have the energy, pass any array whose RMS
   * equals the reading you want. Returns true exactly on the frame that
   * confirms a double clap.
   */
  push(samples: ArrayLike<number>, atMs: number): boolean {
    return this.pushEnergy(rms(samples), atMs);
  }

  /** The same gate, fed a pre-computed energy reading directly -- what the
   *  unit tests use, so a test frame's shape never has to fake real audio. */
  pushEnergy(energy: number, atMs: number): boolean {
    if (!this.seeded) {
      this.seeded = true;
      this.floor = Math.max(this.opts.minFloor, energy);
      return false;
    }
    const threshold = this.floor * this.opts.onsetMultiplier;
    const isAbove = energy > 0 && energy >= threshold;

    if (this.pending) {
      if (!isAbove) {
        // Decayed before the deadline: a real transient, not speech.
        const confirmedAt = this.pending.startedAt;
        this.pending = null;
        this.above = false;
        this.trackFloor(energy);
        return this.confirmOnset(confirmedAt, atMs);
      }
      if (atMs >= this.pending.deadline) {
        // Still elevated past decayMs: speech-shaped, not a clap. The
        // onset itself is discarded, but we stay "above" so the rest of
        // this sustained sound is not read as a fresh onset on every frame.
        this.pending = null;
        return false;
      }
      return false; // still inside the decay window -- keep waiting
    }

    if (isAbove) {
      if (!this.above) {
        // A fresh rising edge.
        this.pending = { startedAt: atMs, deadline: atMs + this.opts.decayMs };
        this.above = true;
      } else {
        // Already above with no pending onset -- a sustained rise (a fan
        // switching on, music, a discarded speech-shaped block) rather than
        // something waiting to be judged. The floor must keep adapting
        // here too, or a room that gets and stays noisier permanently
        // deafens the gate: every later frame would be judged against a
        // floor that never learned the room changed, so nothing above the
        // old, stale floor is ever read as "quiet" again and a real clap
        // riding on top of the new baseline is never seen.
        this.trackFloor(energy);
      }
      return false;
    }

    // Quiet. Track the floor from quiet frames only, so a loud room never
    // teaches the gate to ignore a real clap.
    this.above = false;
    this.trackFloor(energy);
    return false;
  }

  private trackFloor(energy: number): void {
    this.floor = Math.max(
      this.opts.minFloor,
      this.floor + (energy - this.floor) * this.opts.floorAttack,
    );
  }

  private confirmOnset(confirmedAt: number, atMs: number): boolean {
    if (atMs < this.refractoryUntil) {
      // Still cooling down from the last fire -- this onset doesn't even
      // start a new wait.
      this.waitingOnset = null;
      return false;
    }
    if (this.waitingOnset === null) {
      this.waitingOnset = confirmedAt;
      return false;
    }
    const gap = confirmedAt - this.waitingOnset;
    if (gap > this.opts.maxGapMs) {
      // Too slow to be a pair with the first one -- this one becomes the
      // new first half instead of being thrown away.
      this.waitingOnset = confirmedAt;
      return false;
    }
    if (gap < this.opts.minGapMs) {
      // Too fast -- almost certainly the same clap's own echo. Ignore it
      // and keep waiting for a real second onset.
      return false;
    }
    this.waitingOnset = null;
    this.refractoryUntil = atMs + this.opts.refractoryMs;
    return true;
  }
}
