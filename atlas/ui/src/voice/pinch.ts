/**
 * Pinch-to-wake — pure geometry, no DOM.
 *
 * MediaPipe's HandLandmarker reports 21 normalised points per hand (x, y in
 * [0, 1] relative to the frame, z relative to the wrist). This file never
 * touches a camera, a video element, or the model itself: it only measures
 * the distance between two fingertips and debounces the result into a single
 * "wake" edge. Keeping it pure is what makes it fast to test and impossible
 * to accidentally wire into anything that uploads a frame.
 */

export interface Point {
  x: number;
  y: number;
  z?: number;
}

/** Indices into MediaPipe's 21-point hand landmark list. */
export const THUMB_TIP = 4;
export const INDEX_TIP = 8;

/**
 * Distance between thumb tip and index tip, in the frame plane (x, y) only.
 *
 * `z` is deliberately ignored. MediaPipe reports it relative to the wrist,
 * on a noisier and differently-scaled axis than the normalised x/y frame
 * coordinates, and it can only ever inflate this distance. `threshold`
 * below is tuned against x/y alone; folding a noisy z in would push a real
 * pinch above that threshold on some frames and make the gesture flicker
 * shut, never the other way around. `null` when the hand has too few points
 * to trust — a partial or dropped detection, not a real pinch-open reading
 * of 0.
 */
export function pinchDistance(landmarks: Point[]): number | null {
  if (landmarks.length < 9) return null;
  const thumb = landmarks[THUMB_TIP];
  const index = landmarks[INDEX_TIP];
  if (!thumb || !index) return null;
  const dx = thumb.x - index.x;
  const dy = thumb.y - index.y;
  return Math.sqrt(dx * dx + dy * dy);
}

/**
 * True when thumb and index tip are closer than `threshold`. False, never
 * thrown, when the hand cannot be measured — a missing detection is "not
 * pinched", not an error the caller has to guard against.
 */
export function isPinch(landmarks: Point[], threshold = 0.05): boolean {
  const distance = pinchDistance(landmarks);
  return distance !== null && distance < threshold;
}

/**
 * Turns a noisy per-frame pinch/no-pinch reading into a single wake edge.
 *
 * A camera's per-frame pinch classification flickers: a real pinch is not
 * pinched on every frame, and a real release is not open on every frame
 * either. Firing on the first pinched frame would wake on a flicker; never
 * releasing would mean the gesture can only ever fire once per session.
 *
 * `consecutive` pinched frames in a row confirm the gesture and fire exactly
 * once, on that confirming frame. Nothing fires again — even if the hand
 * stays pinched — until `release` consecutive open frames have been seen,
 * which is the debounce that keeps a held pinch from spamming wakes.
 */
export class PinchGate {
  private readonly consecutive: number;
  private readonly release: number;
  private pinchedRun = 0;
  private openRun = 0;
  private armed = true;

  constructor(consecutive = 3, release = 5) {
    this.consecutive = consecutive;
    this.release = release;
  }

  /** Feed one frame's reading. Returns true exactly on the confirming frame. */
  push(pinched: boolean): boolean {
    if (pinched) {
      this.openRun = 0;
      this.pinchedRun += 1;
      if (this.armed && this.pinchedRun >= this.consecutive) {
        this.armed = false;
        return true;
      }
      return false;
    }

    this.pinchedRun = 0;
    this.openRun += 1;
    if (!this.armed && this.openRun >= this.release) {
      this.armed = true;
    }
    return false;
  }
}
