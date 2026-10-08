/**
 * Gesture recognition — pure geometry, no DOM, same discipline as pinch.ts.
 *
 * `classify` turns one frame of MediaPipe's 21 hand landmarks into a single
 * named pose. `GestureStream` turns a sequence of those poses, each with a
 * position and a timestamp, into the handful of events the rest of the app
 * cares about: a cursor to move, a drag to start or end, a swipe through the
 * sidebar, a wake, a dismiss. Neither file touches a camera, a video
 * element, or the model itself — that stays in `useHand`, the one place
 * allowed to open one.
 */
import { pinchDistance, PinchGate, type Point } from "./pinch";

export type Gesture = "none" | "palm" | "fist" | "pinch" | "point" | "two";

export type GestureEvent =
  | { kind: "cursor"; x: number; y: number }
  | { kind: "grab_start" }
  | { kind: "grab_end" }
  | { kind: "swipe"; dir: "left" | "right" }
  | { kind: "wake" }
  | { kind: "dismiss" };

/** Indices into MediaPipe's 21-point hand landmark list, beyond the two
 * pinch.ts already exports (THUMB_TIP = 4, INDEX_TIP = 8). */
const WRIST = 0;
const THUMB_TIP = 4;
const THUMB_IP = 3;
const INDEX_TIP = 8;
const INDEX_PIP = 6;
const MIDDLE_TIP = 12;
const MIDDLE_PIP = 10;
const RING_TIP = 16;
const RING_PIP = 14;
const PINKY_TIP = 20;
const PINKY_PIP = 18;

const PINCH_THRESHOLD = 0.05; // Task 16's threshold, unchanged.

/** A finger counts as extended when its tip is above (smaller y than) its
 * own pip joint -- pointing away from the palm rather than curled over it. */
function fingerExtended(landmarks: Point[], tip: number, pip: number): boolean {
  return (landmarks[tip]?.y ?? 0) < (landmarks[pip]?.y ?? 0);
}

/** The thumb does not curl the way the other four fingers do -- it swings
 * sideways, in x, rather than folding down in y. "4 vs 3" compares the tip
 * (4) and the IP joint (3) by how far each sits from the wrist in x: a
 * tucked thumb's tip sits no farther out than its own IP joint, an extended
 * one sits clearly farther. Measuring from the wrist rather than testing a
 * fixed direction is what makes this work for either hand, mirrored or not. */
function thumbExtended(landmarks: Point[]): boolean {
  const wrist = landmarks[WRIST];
  const tip = landmarks[THUMB_TIP];
  const ip = landmarks[THUMB_IP];
  if (!wrist || !tip || !ip) return false;
  return Math.abs(tip.x - wrist.x) > Math.abs(ip.x - wrist.x);
}

/**
 * One frame, one pose. `pinch` is checked first and wins outright -- a
 * thumb and index tip drawn together is a deliberate, specific gesture, and
 * nothing about how the other fingers happen to be sitting should be able
 * to override it.
 */
export function classify(landmarks: Point[]): Gesture {
  if (landmarks.length < 21) return "none";

  const distance = pinchDistance(landmarks);
  if (distance !== null && distance < PINCH_THRESHOLD) return "pinch";

  const index = fingerExtended(landmarks, INDEX_TIP, INDEX_PIP);
  const middle = fingerExtended(landmarks, MIDDLE_TIP, MIDDLE_PIP);
  const ring = fingerExtended(landmarks, RING_TIP, RING_PIP);
  const pinky = fingerExtended(landmarks, PINKY_TIP, PINKY_PIP);

  if (index && middle && ring && pinky) return "palm";
  if (!index && !middle && !ring && !pinky && !thumbExtended(landmarks)) return "fist";
  if (index && !middle && !ring && !pinky) return "point";
  if (index && middle && !ring && !pinky) return "two";
  return "none";
}

type Mode = "idle" | "cursor" | "grab";

const PALM_HOLD_MS = 400;
const TWO_HOLD_MS = 600;
const SWIPE_WINDOW_MS = 350;
const SWIPE_DISTANCE = 0.35;

/**
 * Turns a stream of classified poses into the events `useHand` broadcasts.
 *
 * One instance per camera session (owned by the shared controller in
 * `useHand.ts`, never one per consumer) -- the mode it tracks (idle, cursor,
 * grab) is a property of the hand being watched, not of who is watching it.
 */
export class GestureStream {
  private mode: Mode = "idle";
  private palmHoldGesture: Gesture = "none";
  private palmHoldSince = 0;
  private palmHistory: { t: number; x: number }[] = [];
  // `null` means "not currently held" -- 0 cannot be used as that sentinel
  // because a hold can legitimately begin on the very first frame, at t=0.
  private twoSince: number | null = null;
  private twoFired = false;
  // The exact debounce pinch-to-wake used before this file existed: three
  // consecutive pinched frames confirm, five open frames re-arm. Reused, not
  // re-tuned, so wake behaves identically to Task 16.
  private readonly pinchGate = new PinchGate();

  push(g: Gesture, palmX: number, palmY: number, t: number): GestureEvent | null {
    const pinchConfirmed = this.pinchGate.push(g === "pinch");

    // "two", held: dismiss, once. Works regardless of mode -- it is the
    // escape hatch out of cursor or grab as much as out of the voice
    // overlay, so it also drops the hand back to idle below.
    if (g === "two") {
      if (this.twoSince === null) {
        this.twoSince = t;
        this.twoFired = false;
      } else if (!this.twoFired && t - this.twoSince >= TWO_HOLD_MS) {
        this.twoFired = true;
        this.mode = "idle";
        this.palmHoldGesture = "none";
        this.palmHoldSince = 0;
        return { kind: "dismiss" };
      }
    } else {
      this.twoSince = null;
      this.twoFired = false;
    }

    // A fast palm sweep: > 0.35 of the frame width inside the trailing
    // 350ms window. Checked ahead of the cursor-hold logic below so a quick
    // swipe can never also read as the start of a held palm.
    if (g === "palm") {
      this.palmHistory.push({ t, x: palmX });
      while (this.palmHistory.length > 1
        && t - (this.palmHistory[0]?.t ?? t) > SWIPE_WINDOW_MS) {
        this.palmHistory.shift();
      }
      const oldest = this.palmHistory[0];
      if (oldest && this.palmHistory.length > 1) {
        const dx = palmX - oldest.x;
        if (Math.abs(dx) > SWIPE_DISTANCE) {
          this.palmHistory = [];
          this.palmHoldGesture = "none";
          this.palmHoldSince = 0;
          return { kind: "swipe", dir: dx < 0 ? "left" : "right" };
        }
      }
    } else {
      this.palmHistory = [];
    }

    // Pinch wakes, but only outside a cursor/grab session -- a pinch made
    // while reaching for a tile must not also sound the wake word.
    if (pinchConfirmed && this.mode === "idle") {
      return { kind: "wake" };
    }

    if (this.mode === "idle") {
      if (g !== "palm") {
        this.palmHoldGesture = g;
        return null;
      }
      if (this.palmHoldGesture !== "palm") {
        this.palmHoldGesture = "palm";
        this.palmHoldSince = t;
        return null;
      }
      if (t - this.palmHoldSince < PALM_HOLD_MS) return null;
      this.mode = "cursor";
      return { kind: "cursor", x: palmX, y: palmY };
    }

    if (this.mode === "cursor") {
      if (g === "fist") {
        this.mode = "grab";
        return { kind: "grab_start" };
      }
      // Only the palm itself moves the cursor. Anything else the hand does
      // here (a pinch, a point, an open "two") is not a gesture this mode
      // understands, so it is ignored rather than read as a cursor move --
      // a pinch made while reaching for a tile must not also wake him.
      if (g === "palm") return { kind: "cursor", x: palmX, y: palmY };
      return null;
    }

    // this.mode === "grab": the fist is dragging something.
    if (g === "palm") {
      this.mode = "cursor";
      return { kind: "grab_end" };
    }
    if (g === "fist") return { kind: "cursor", x: palmX, y: palmY };
    return null;
  }
}
