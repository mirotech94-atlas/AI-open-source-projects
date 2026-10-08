import {
  HEAD_C, HEAD_R, NECK_BOTTOM, NECK_R, NECK_TOP,
  SHOULDER_DEPTH, SHOULDER_HALF, faceCentre, prng,
} from "./bust-geometry";
import type { Phase } from "../voice/loop";

/**
 * The drawn structure over the particle cloud: a face and a silhouette, as
 * pure geometry and pure expression rules. No DOM, no `three`, no canvas —
 * `Bust.tsx` projects these ATLAS-unit points through the camera and draws
 * them; this module only decides *where* they are and *how open* an eye or
 * a mouth is right now.
 *
 * The same rule the particle cloud already follows applies here: every
 * number is either a fixed shape, the clock, or a real measurement.
 * `mouthOpen` is `level` — the speaker's own amplitude — and nothing else
 * moves without a phase or the clock behind it. Blinking, the idle breath
 * and the thinking orbit are clock-driven ambient motion, the same kind the
 * bust's existing idle yaw and jitter already are; none of them claims to
 * measure anything, so none of them is disabled by anything except
 * `prefers-reduced-motion`, which turns all three off, along with the head
 * tilt this module also gates on it.
 */

export type Gaze = { x: number; y: number } | null;

export interface FaceInput {
  phase: Phase;
  /** The loudest bar this frame, 0..1. Only read while `phase` is
   *  "responding" — a loud room is not ATLAS talking. */
  level: number;
  /** Milliseconds on the same clock the stage's own `t` is built from. */
  tMs: number;
  /** Seeds the blink cadence, so a given seed always blinks on the same
   *  schedule — the bust's own reasoning for a seeded cloud, applied to
   *  the face. */
  seed: number;
  reducedMotion: boolean;
  /** Where the cursor or the tracked hand is, normalised 0..1 across the
   *  viewport, or null when neither is known. */
  gaze: Gaze;
}

export interface FaceState {
  /** 0 (shut) .. 1 (normal open). */
  eyeOpen: number;
  /** -1 (furrowed) .. 1 (lifted). */
  browRaise: number;
  /** 0 (closed) .. 1 (fully open). */
  mouthOpen: number;
  /** 0 (neutral) .. 1 (full smile). */
  smile: number;
  /** Head tilt, degrees. Nonzero only while listening. */
  tiltDeg: number;
  /** Small vertical offset, ATLAS units. Nonzero only while dormant. */
  breathY: number;
  /** Orbit angle in radians, or null when nothing should orbit. */
  orbit: number | null;
  /** Whether a blink is happening on this frame. */
  blinking: boolean;
  /** Pupil offset, -1..1 each axis. 0,0 when `gaze` is null. */
  gazeX: number;
  gazeY: number;
}

/** How wide the eyes sit, normally, per phase. Dormant is half-closed —
 *  never shut — and listening opens a touch wider than the idle rest. */
const BASE_EYE_OPEN: Record<Phase, number> = {
  dormant: 0.35,
  waking: 0.75,
  listening: 0.95,
  thinking: 0.55,
  responding: 0.8,
  dismissing: 0.6,
};

/** A slight smile at rest; thinking and dismissing pull it in a little,
 *  responding narrows it further because the mouth is busy talking. */
const BASE_SMILE: Record<Phase, number> = {
  dormant: 0.4,
  waking: 0.35,
  listening: 0.35,
  thinking: 0.22,
  responding: 0.15,
  dismissing: 0.2,
};

/** Degrees the head tilts toward the mic while listening. */
const LISTEN_TILT_DEG = 6;

/** How far the idle breath moves the head, ATLAS units, and how slowly. */
const BREATH_AMPLITUDE = 0.012;
const BREATH_PERIOD_MS = 4200;

/** How fast the thinking orbit turns. */
const ORBIT_SPEED = 0.9; // radians / second

/** Average time between blinks, and how long one lasts. Real people blink
 *  roughly every 2–6 seconds; this sits in the middle of that. */
const BLINK_PERIOD_MS = 3400;
const BLINK_DURATION_MS = 130;

/** How much a known gaze point pulls the pupils, and how far a gaze at the
 *  very edge of the viewport can push them — a look, not a stare. */
const GAZE_MAX = 0.6;

/** Whether frame `tMs` falls inside a blink, for the given seed. A closed
 *  form rather than a loop over history: each `BLINK_PERIOD_MS` window gets
 *  its own jittered blink, derived from a hash of the window index, so the
 *  schedule is the same however `tMs` is sampled and never accumulates. */
export function isBlinking(tMs: number, seed: number): boolean {
  if (tMs < 0) return false;
  const index = Math.floor(tMs / BLINK_PERIOD_MS);
  const rand = prng(seed + index * 104729)();
  const start = index * BLINK_PERIOD_MS
    + rand * (BLINK_PERIOD_MS - BLINK_DURATION_MS);
  return tMs >= start && tMs < start + BLINK_DURATION_MS;
}

function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}

export function faceState(input: FaceInput): FaceState {
  const { phase, level, tMs, seed, reducedMotion, gaze } = input;

  const blinking = !reducedMotion && isBlinking(tMs, seed);
  const eyeOpen = blinking ? 0.04 : BASE_EYE_OPEN[phase];

  // Thinking narrows further with a slow furrow; listening lifts a touch.
  const browRaise = phase === "thinking" ? -0.35 : phase === "listening" ? 0.3 : 0;

  const speaking = phase === "responding";
  const mouthOpen = speaking ? clamp(level, 0, 1) : 0;
  const smile = speaking ? BASE_SMILE.responding * (1 - mouthOpen * 0.6) : BASE_SMILE[phase];

  const tiltDeg = phase === "listening" ? LISTEN_TILT_DEG : 0;

  const breathY = phase === "dormant" && !reducedMotion
    ? Math.sin((tMs / BREATH_PERIOD_MS) * Math.PI * 2) * BREATH_AMPLITUDE
    : 0;

  const orbit = phase === "thinking" && !reducedMotion
    ? (tMs / 1000) * ORBIT_SPEED % (Math.PI * 2)
    : null;

  const gazeX = gaze ? clamp((gaze.x - 0.5) * 2, -1, 1) * GAZE_MAX : 0;
  const gazeY = gaze ? clamp((gaze.y - 0.5) * 2, -1, 1) * GAZE_MAX : 0;

  return { eyeOpen, browRaise, mouthOpen, smile, tiltDeg, breathY, orbit, blinking, gazeX, gazeY };
}

// --- static landmarks and outline paths -----------------------------------
//
// All in ATLAS units, on the same head the particle cloud is built from
// (`HEAD_C`/`HEAD_R` etc., imported rather than repeated). `Bust.tsx`
// projects each point through the camera; nothing here knows about pixels.

export type Point3 = readonly [number, number, number];

const [HX, HY, HZ] = HEAD_R;
const [CX, CY, CZ] = HEAD_C;

/** Front-of-head plane the face sits on. */
const FACE_Z = CZ + HZ * 0.94;

export const EYE_L: Point3 = [CX - HX * 0.34, CY + HY * 0.06, FACE_Z];
export const EYE_R: Point3 = [CX + HX * 0.34, CY + HY * 0.06, FACE_Z];
export const BROW_L: Point3 = [CX - HX * 0.34, CY + HY * 0.24, FACE_Z * 0.99];
export const BROW_R: Point3 = [CX + HX * 0.34, CY + HY * 0.24, FACE_Z * 0.99];
export const NOSE_TOP: Point3 = [CX, CY, FACE_Z * 1.01];
export const NOSE_BOTTOM: Point3 = [CX, CY - HY * 0.2, FACE_Z * 1.03];
export const MOUTH_CENTER: Point3 = [CX, CY - HY * 0.42, FACE_Z];
/** Eye half-width/height and mouth half-width, in ATLAS units — shared by
 *  `Bust.tsx` so the drawn ellipse sizes stay tied to the head's own scale
 *  rather than a second set of hand-picked pixel constants. */
export const EYE_RX = HX * 0.115;
export const EYE_RY = HY * 0.085;
export const MOUTH_HALF = HX * 0.26;

/** The core the amber cluster already sits in — the "heart-light" the face
 *  is drawn in front of. Re-exported under this module's naming for the
 *  orbit ring, which circles the same point. */
export const CORE_CENTER = faceCentre;
export const ORBIT_RADIUS = 0.30;

/** A smooth silhouette from crown to jaw, one side; `Bust.tsx` mirrors it
 *  for the other side and strokes the pair as the head/jaw outline. Uses
 *  the same shell taper the particle cloud's head shell uses, at the
 *  surface exactly (no shell jitter), so the outline traces the cloud
 *  rather than floating in front of or behind it. */
export function headProfile(steps = 24): Point3[] {
  const points: Point3[] = [];
  for (let i = 0; i <= steps; i += 1) {
    // +1 (crown) .. -1 (chin) — the same sign the head-shell sampler uses,
    // so `dy < -0.3` picks out the jaw here exactly as it does there.
    const dy = 1 - (2 * i) / steps;
    const r = Math.sqrt(Math.max(0, 1 - dy * dy));
    const taper = dy < -0.3 ? 0.88 : 1;
    points.push([
      HEAD_C[0] + r * HEAD_R[0] * taper,
      HEAD_C[1] + dy * HEAD_R[1],
      HEAD_C[2] + r * HEAD_R[2] * 0.55,
    ]);
  }
  return points;
}

/** One side of the neck, jaw to shoulder — the same cylinder the neck
 *  particles fill, taken at its front-facing edge (angle 0). */
export function neckProfile(steps = 8): Point3[] {
  const points: Point3[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const y = NECK_TOP - t * (NECK_TOP - NECK_BOTTOM);
    const r = NECK_R * (0.86 + 0.14 * (1 - t));
    points.push([r, y, 0]);
  }
  return points;
}

/** One side of the shoulder arc, neck to the outer edge — the top ridge of
 *  the same falling arc the shoulder particles fill. */
export function shoulderProfile(steps = 14): Point3[] {
  const points: Point3[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps; // 0 at the neck, 1 at the shoulder's outer edge.
    const across = t * SHOULDER_HALF;
    const drop = 0.30 - 0.46 * t * t;
    const thickness = 0.24 * (1 - 0.45 * t);
    points.push([across, drop + thickness * 0.5, SHOULDER_DEPTH * (0.6 + 0.4 * (1 - t)) * 0.5]);
  }
  return points;
}
