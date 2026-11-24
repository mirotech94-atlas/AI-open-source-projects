/**
 * The speaking sparks: green and teal particles that rise and drift from the
 * core while ATLAS talks, as many and as fast as his voice is loud.
 *
 * The level is the real one -- the analyser on the clip that is playing
 * (`useVoice.outputLevel`), never a timer pretending to be a voice. When the
 * clip is silent the level is 0, nothing new is born, and what is in the air
 * fades out in a second or so.
 *
 * Pure: a field of particles and the arithmetic to step it. The canvas that
 * draws it is `SpeakingSparks.tsx`; there is no dependency here.
 */

export interface Spark {
  x: number;
  y: number;
  vx: number;
  vy: number;
  /** Seconds lived and seconds it will live. */
  age: number;
  life: number;
  size: number;
  /** 0 = green, 1 = teal. */
  hue: number;
  /** Phase and speed of the sideways wobble. */
  wobble: number;
  sway: number;
}

export interface Origin {
  /** The core's centre and radius, in canvas pixels. */
  x: number;
  y: number;
  r: number;
}

/** Below this a voice is silence: the analyser's noise floor is not speech. */
export const SILENT_LEVEL = 0.04;
export const MAX_SPARKS = 160;
/** Sparks born per second at full level. */
const MAX_RATE = 90;
/** A frame longer than this is a stall (a hidden tab), not time that passed. */
const MAX_DT = 0.1;

/**
 * 0..1 from a time-domain byte buffer (silence is 128): the RMS, shaped the
 * way the waveform bars are, so the sparks and the bars agree on what loud is.
 */
export function levelOf(bytes: ArrayLike<number>): number {
  const n = bytes.length;
  if (n === 0) return 0;
  let sum = 0;
  for (let i = 0; i < n; i += 1) {
    const sample = (bytes[i] ?? 128) - 128;
    sum += sample * sample;
  }
  const rms = Math.sqrt(sum / n) / 128;
  return Math.min(1, Math.pow(rms * 3.2, 0.7));
}

/** Sparks per second for a level; none at all in silence. */
export function spawnRate(level: number): number {
  if (level < SILENT_LEVEL) return 0;
  return MAX_RATE * Math.min(1, level);
}

export class SparkField {
  readonly sparks: Spark[] = [];
  private carry = 0;

  constructor(private readonly random: () => number = Math.random) {}

  /** Advance `dt` seconds, with the voice at `level`, born around `origin`. */
  step(dt: number, level: number, origin: Origin): void {
    const seconds = Math.min(Math.max(dt, 0), MAX_DT);
    if (seconds === 0) return;

    this.carry += spawnRate(level) * seconds;
    while (this.carry >= 1) {
      this.carry -= 1;
      if (this.sparks.length < MAX_SPARKS) this.sparks.push(this.born(level, origin));
    }
    if (spawnRate(level) === 0) this.carry = 0;

    for (const s of this.sparks) {
      s.age += seconds;
      s.wobble += s.sway * seconds;
      s.x += (s.vx + Math.sin(s.wobble) * 14) * seconds;   // drift
      s.y += s.vy * seconds;                               // rise (y is down)
      s.vy *= 1 - 0.35 * seconds;                          // slows as it climbs
    }
    for (let i = this.sparks.length - 1; i >= 0; i -= 1) {
      const s = this.sparks[i];
      if (s && s.age >= s.life) this.sparks.splice(i, 1);
    }
  }

  clear(): void {
    this.sparks.length = 0;
    this.carry = 0;
  }

  private born(level: number, o: Origin): Spark {
    const r = this.random;
    // Anywhere on the upper half of the core's rim, a little outside it.
    const angle = Math.PI + r() * Math.PI;
    const radius = o.r * (0.85 + r() * 0.35);
    return {
      x: o.x + Math.cos(angle) * radius,
      y: o.y + Math.sin(angle) * radius,
      vx: (r() - 0.5) * 26,
      vy: -(26 + r() * 40 + level * 55),
      age: 0,
      life: 0.9 + r() * 1.1,
      size: 1 + r() * 2 + level * 1.2,
      hue: r(),
      wobble: r() * Math.PI * 2,
      sway: 3 + r() * 5,
    };
  }
}
