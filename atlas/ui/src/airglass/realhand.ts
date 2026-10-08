// A realistic hand for tests and the demo: built in the camera's own picture
// (normalised, un-mirrored, as MediaPipe reports it), then mapped to the
// stage exactly as the live camera path maps it. Unlike a hand drawn straight
// onto the stage, it knows where the picture ends -- the thing a real hand
// cannot leave without the tracker losing it.
import { synthHand } from "./demo";
import { LM, pinchPoint, screenToVideo, videoToScreen, type Pt, type Size } from "./gestures";

export interface RealHandOptions {
  /** Where the pinch point should be, in STAGE pixels. */
  at: Pt;
  view: Size;
  video: Size;
  /** Palm size as a share of picture height (0.17: ~55 cm from the camera). */
  palmFrac?: number;
  /** 0 open .. 1 closed. A closed real pinch leaves a gap of `gap` palms
   * between the tip landmarks (they sit at the ends of the fingers, not on
   * the pads that touch). */
  pinch?: number;
  gap?: number;
  /** Fingers tilted toward the camera, radians. */
  pitch?: number;
  /** MediaPipe's z is noisy and under-scaled; this scales it. */
  zScale?: number;
  left?: boolean;
  /** Pixel noise (picture pixels), from a seeded generator. */
  jitter?: number;
  seed?: () => number;
}

/** A seeded uniform generator in [-0.5, 0.5). */
export function rng(seed = 7): () => number {
  let s = seed;
  return () => ((s = (s * 16807) % 2147483647) / 2147483647) - 0.5;
}

/** The 21 landmarks in the picture's normalised coordinates. */
export function rawHand(o: RealHandOptions): Pt[] {
  const { video } = o;
  const palm = (o.palmFrac ?? 0.17) * video.h;
  const target = screenToVideo(o.at, video, o.view, true);
  const pinch = o.pinch ?? 0;
  const lm = synthHand({ x: 0, y: 0, size: palm, pinch, pitch: o.pitch ?? 0, left: o.left ?? false });
  // The residual gap of a real closed pinch, across the hand.
  const gap = (o.gap ?? 0.17) * palm * pinch;
  const thumb = lm[LM.THUMB_TIP], index = lm[LM.INDEX_TIP];
  if (thumb && index && gap > 0) {
    lm[LM.THUMB_TIP] = { ...thumb, x: thumb.x + gap / 2 };
    lm[LM.INDEX_TIP] = { ...index, x: index.x - gap / 2 };
  }
  const p = pinchPoint(lm);
  const dx = target.x * video.w - p.x, dy = target.y * video.h - p.y;
  const jit = o.jitter ?? 0, r = o.seed ?? rng();
  const zs = o.zScale ?? 1;
  return lm.map((q) => ({
    x: (q.x + dx + jit * r()) / video.w,
    y: (q.y + dy + jit * r()) / video.h,
    z: ((q.z ?? 0) * zs) / video.w,
  }));
}

/** The same hand on the stage, as the camera path would hand it on. */
export function stageHand(o: RealHandOptions): Pt[] {
  return rawHand(o).map((p) => videoToScreen(p, o.video, o.view, true));
}

/** Every landmark at least `edge` inside the picture: the tracker can see it. */
export function inPicture(raw: readonly Pt[], edge = 0.03): boolean {
  return raw.every((p) => p.x >= edge && p.x <= 1 - edge && p.y >= edge && p.y <= 1 - edge);
}
