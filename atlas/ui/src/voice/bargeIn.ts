/**
 * Barge-in: Miro speaking over ATLAS, as pure rules -- no DOM, no timers.
 *
 * "If I speak over ATLAS then I should be able to interrupt." The hard part
 * is not hearing him; it is NOT hearing ATLAS. The mic runs with echo
 * cancellation on, but a canceller leaves a residue, some browsers and
 * output devices defeat it outright, and on a laptop with the speakers up
 * the leak can be loud. An interrupt that fires on ATLAS's own voice cuts
 * every answer off a second in, which is worse than no barge-in at all.
 *
 * So three things must hold, for long enough to be a person talking and not
 * a cough, a door, or one loud syllable of ATLAS leaking back:
 *
 *   1. The mic is above a speaking level of its own (the room's quiet floor,
 *      the way a listening turn sets it, never below an absolute minimum).
 *   2. The mic is clearly above what ATLAS's own playback would explain. The
 *      speaker analyser says how loud ATLAS is; the mic hears it a little
 *      later (output buffer, air, the canceller's latency) and a word's
 *      reverb outlasts the word, so the comparison is against a decaying
 *      peak of the speaker -- `echo` -- not the instant value.
 *   3. How much of ATLAS the mic hears at all -- the `coupling` -- is
 *      MEASURED, over the first moments of each answer, rather than assumed.
 *      With the canceller working it is small and he can interrupt at a
 *      normal voice; with it defeated it is large and he has to be clearly
 *      louder than ATLAS. Nothing fires until it has been measured. If he
 *      talks during that window the measurement comes out high, which only
 *      makes interrupting harder until a quiet second lets it be re-read:
 *      the failure is always ATLAS finishing its sentence, never ATLAS
 *      cutting itself off.
 *
 * All of it must hold for HOLD_MS, with dips shorter than GAP_MS forgiven
 * (speech is not a flat line). No speaker reading at all (`speaker: null`:
 * the voice is playing unrouted because the audio context was not running)
 * never fires: his voice and ATLAS's echo cannot be told apart without it.
 *
 * What was wrong (2026-09-29, "I can't talk over it"): the bar had a fixed
 * part, `echo x 0.3 + 0.06`. ATLAS's analyser reads the digital signal
 * before the volume knob, so while it talks `echo` sits near 1 (measured on
 * normalised speech: median 0.86, often 1) and the bar sat at 0.32-0.36 --
 * above ordinary speech on this scale (0.28) however well the canceller
 * worked. Now the only part that scales with ATLAS is the measured coupling.
 * `bargeIn.realistic.test.ts` replays real playback levels against it.
 */

/** How long he must keep talking over ATLAS before it stops. */
export const BARGE_IN_HOLD_MS = 280;
/** A dip in his level shorter than this does not restart the hold: the gap
 *  between two syllables, not the pause between two sentences. */
export const BARGE_IN_GAP_MS = 140;
/** Never interrupt below this, whatever the room says. `level` is the mic's
 *  shaped peak: idle near 0.055, ordinary speech near 0.28. */
export const BARGE_IN_MIN_LEVEL = 0.16;
/** Time constant of the speaker's decaying peak. */
export const ECHO_DECAY_MS = 220;
/** How long each answer's coupling is measured before barge-in arms... */
export const WARMUP_MS = 450;
/** ...counting only frames where ATLAS is loud enough to leak at all; if it
 *  never is, it keeps measuring up to WARMUP_MAX_MS and then arms. */
export const ECHO_AUDIBLE = 0.2;
export const WARMUP_MIN_FRAMES = 12;
export const WARMUP_MAX_MS = 1500;
/** The mic must exceed idle + echo x coupling x HEADROOM + MARGIN. */
export const ECHO_HEADROOM = 2;
export const ECHO_MARGIN = 0.05;
/** Which of the per-frame leak ratios stands for the coupling: a high one,
 *  not the highest -- one transient must not set it. */
export const COUPLING_PERCENTILE = 0.8;
/** After arming, the coupling is re-read over this much recent playback.
 *  The re-read may only LOWER it (the canceller converging, or him having
 *  talked through the measuring window). It is a high percentile over a
 *  second of ATLAS's words, never a per-frame minimum, so the gaps between
 *  words cannot walk it down to nothing. */
export const RECENT_MS = 1200;
const COUPLING_MIN = 0.03;
const COUPLING_MAX = 1.5;
/** ATLAS never audible in the window: nothing was measured, so assume a
 *  leak as bad as a laptop with its canceller defeated. */
const COUPLING_UNMEASURED = 0.6;
// The floor is the quietest the room was during the last listening turn.
// Capped, so a turn that never found its quiet (the floor starts at 1)
// cannot push the threshold out of reach.
const FLOOR_CAP = 0.15;
const FLOOR_MULTIPLE = 1.9;
const FLOOR_MARGIN = 0.03;

export interface BargeInState {
  /** When the current run of his speech began, or null when there is none. */
  heldSince: number | null;
  /** The last frame that qualified, for the gap allowance. */
  lastHeard: number;
  /** ATLAS's recent peak output, decaying. */
  echo: number;
  /** When `echo` was last updated; null before the first frame. */
  at: number | null;
  /** Mic level per unit of ATLAS's level, once measured; null while not. */
  coupling: number | null;
  /** The measuring window: when it opened, and its per-frame leak ratios. */
  warmSince: number | null;
  warm: number[];
  /** Leak ratios since arming, oldest first, for the re-read. */
  recent: { at: number; ratio: number }[];
  /** Already interrupted this answer: never again until a fresh state. */
  fired: boolean;
}

export interface BargeInReading {
  now: number;
  /** The mic's shaped peak level, 0..1 (`Mic.read().level`). */
  mic: number;
  /** ATLAS's own output, same shaping, or null when it cannot be read. */
  speaker: number | null;
  /** The room's quiet floor, as the listening loop measured it. */
  floor: number;
}

/** A fresh state, for the start of each spoken answer. */
export function initialBargeIn(): BargeInState {
  return {
    heldSince: null, lastHeard: 0, echo: 0, at: null,
    coupling: null, warmSince: null, warm: [], recent: [], fired: false,
  };
}

const idleOf = (floor: number): number => Math.min(Math.max(floor, 0), FLOOR_CAP);

/**
 * The level the mic must beat, given ATLAS's recent output.
 *
 * What the mic hears of ATLAS is the room's idle level plus `coupling` x
 * ATLAS's level -- measured, never assumed. There is deliberately no fixed
 * fraction of ATLAS's level here: its analyser sits near 1 whenever it
 * talks, so any fixed fraction of it is a fixed bar.
 */
export function bargeInThreshold(echo: number, floor: number, coupling: number): number {
  const idle = idleOf(floor);
  const room = idle * FLOOR_MULTIPLE + FLOOR_MARGIN;
  return Math.max(
    BARGE_IN_MIN_LEVEL, room, idle + echo * coupling * ECHO_HEADROOM + ECHO_MARGIN,
  );
}

const clamp = (value: number, low: number, high: number): number =>
  Math.min(high, Math.max(low, value));

/** The `p` quantile of some values (nearest rank), or null when empty. */
export function percentile(values: readonly number[], p: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1));
  return sorted[index] ?? null;
}

/**
 * One frame. Returns the next state and whether to interrupt now. Fires at
 * most once per state: a fresh one (`initialBargeIn`) per spoken answer.
 */
export function stepBargeIn(
  state: BargeInState,
  reading: BargeInReading,
): { state: BargeInState; interrupt: boolean } {
  const { now, mic, speaker, floor } = reading;
  if (state.fired) return { state, interrupt: false };
  const dt = state.at === null ? 0 : Math.max(0, now - state.at);
  const decayed = state.echo * Math.exp(-dt / ECHO_DECAY_MS);

  if (speaker === null) {
    // Blind to ATLAS's own output: never fire, forget any hold, and do not
    // let a blind stretch count towards measuring the coupling.
    return {
      state: { ...state, heldSince: null, lastHeard: 0, echo: decayed, at: now },
      interrupt: false,
    };
  }
  const echo = Math.max(speaker, decayed);
  // This frame's leak: how much of the mic, above the room, ATLAS explains.
  const ratio = echo >= ECHO_AUDIBLE ? Math.max(0, mic - idleOf(floor)) / echo : null;

  if (state.coupling === null) {
    const warmSince = state.warmSince ?? now;
    const warm = ratio === null ? state.warm : [...state.warm, ratio];
    const elapsed = now - warmSince;
    const enough = elapsed >= WARMUP_MS && warm.length >= WARMUP_MIN_FRAMES;
    let coupling: number | null = null;
    if (enough || elapsed >= WARMUP_MAX_MS) {
      const measured = percentile(warm, COUPLING_PERCENTILE);
      coupling = measured === null
        ? COUPLING_UNMEASURED
        : clamp(measured, COUPLING_MIN, COUPLING_MAX);
    }
    return {
      state: {
        ...state, heldSince: null, lastHeard: 0, echo, at: now,
        coupling, warmSince, warm, recent: [],
      },
      interrupt: false,
    };
  }

  // The re-read: a high percentile over the last RECENT_MS of audible
  // playback, which may only lower the coupling.
  let recent = state.recent.filter((r) => now - r.at <= RECENT_MS);
  if (ratio !== null) recent = [...recent, { at: now, ratio }];
  let coupling = state.coupling;
  const oldest = recent[0];
  if (oldest && now - oldest.at >= RECENT_MS * 0.9) {
    const reread = percentile(recent.map((r) => r.ratio), COUPLING_PERCENTILE);
    if (reread !== null) coupling = Math.min(coupling, Math.max(reread, COUPLING_MIN));
  }

  const heard = mic > bargeInThreshold(echo, floor, coupling);

  let heldSince = state.heldSince;
  let lastHeard = state.lastHeard;
  if (heard) {
    if (heldSince === null || now - lastHeard > BARGE_IN_GAP_MS) heldSince = now;
    lastHeard = now;
  } else if (heldSince !== null && now - lastHeard > BARGE_IN_GAP_MS) {
    heldSince = null;
  }

  if (heard && heldSince !== null && now - heldSince >= BARGE_IN_HOLD_MS) {
    return {
      state: {
        ...state, heldSince: null, lastHeard: 0, echo, at: now, coupling, recent,
        fired: true,
      },
      interrupt: true,
    };
  }
  return {
    state: { ...state, heldSince, lastHeard, echo, at: now, coupling, recent },
    interrupt: false,
  };
}

/** The loudest bar of a reading, the same "peak" `Mic.read` reports. */
export function peakOf(bars: readonly number[] | null): number | null {
  if (!bars) return null;
  let peak = 0;
  for (const bar of bars) peak = Math.max(peak, bar);
  return peak;
}
