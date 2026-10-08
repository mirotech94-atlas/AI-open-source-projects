// Where Air Glass puts what a hand has to reach. Pure, so reach.test.ts can
// prove every target is reachable by a real raised hand -- palm in the
// camera's frame -- before it is drawn.
//
// The rule behind it: MediaPipe finds a hand by its palm. A pinch point is
// about 1.15 palm sizes above the wrist when the hand is raised, and the
// heel of the hand reaches about 0.35 below the wrist, so the lowest point a
// pinch can reach is ~1.5 palm sizes above the bottom of the PICTURE (not of
// the stage: object-fit: cover crops the picture to fit). The old tray sat
// lower than that, which is why a real forefinger could not pick from it.
import { coverFit, type Pt, type Rect, type Size } from "./gestures";

/** Assumed until the camera reports its own size. */
export const VIDEO_DEFAULT: Size = { w: 1280, h: 720 };

export const REACH = Object.freeze({
  /** Palm size (wrist to middle MCP) as a share of picture height: a hand
   * about 55 cm from a laptop camera. Nearer is bigger, which only helps. */
  palmFrac: 0.17,
  pinchAbove: 1.15,   // pinch point above the wrist, palm sizes (hand raised)
  heelBelow: 0.35,    // hand below the wrist landmark the detector needs to see
  fingersAbove: 0.75, // index and thumb above the pinch point
  sideways: 0.75,     // the hand either side of its pinch point
  edge: 0.03,         // keep landmarks this far inside the picture (share)
});

/** The picture's own rectangle on the stage (it can overhang under cover). */
export function pictureRect(view: Size, video: Size): Rect {
  const { s, ox, oy } = coverFit(video, view);
  return { x: ox, y: oy, w: video.w * s, h: video.h * s };
}

/**
 * The stage box inside which a raised hand, palm in frame, can put its
 * pinch. `palmPx`: the palm size seen right now on the stage, when a hand is
 * up; otherwise the typical one.
 */
export function reachBox(view: Size, video: Size, palmPx: number | null = null): Rect {
  const pic = pictureRect(view, video);
  const palm = palmPx ?? REACH.palmFrac * pic.h;
  const e = REACH.edge;
  const top = Math.max(0, pic.y + e * pic.h + REACH.fingersAbove * palm);
  const bottom = Math.min(view.h, pic.y + (1 - e) * pic.h - (REACH.pinchAbove + REACH.heelBelow) * palm);
  const left = Math.max(0, pic.x + e * pic.w + REACH.sideways * palm);
  const right = Math.min(view.w, pic.x + (1 - e) * pic.w - REACH.sideways * palm);
  return { x: left, y: top, w: Math.max(0, right - left), h: Math.max(0, bottom - top) };
}

export interface Layout {
  tile: Size;
  /** The row of tiles (centred on the stage). */
  tray: Rect;
  /** The button column on the left: +, -, Ask ATLAS, Explain, Give. */
  side: Rect;
  zone: Rect;
  reach: Rect;
}

export const SIDE_W = 132;
export const SIDE_H = 318;
export const ZONE = { w: 164, h: 224 } as const;
const GAP = 14;
const MARGIN = 14;

/**
 * Lay out the targets for this stage and picture. `tiles` is how many tiles
 * the tray shows; `palmPx` is the palm seen now (null: the typical one), so a
 * hand held far from the camera -- smaller, reaching lower -- lets the tray
 * sit lower, and one held near pulls it up.
 */
export function layoutFor(view: Size, video: Size, tiles: number,
                          palmPx: number | null = null): Layout {
  const reach = reachBox(view, video, palmPx);
  const tw = Math.round(Math.min(168, Math.max(128, view.w * 0.11)));
  const tile = { w: tw, h: Math.round(tw * 0.82) };
  const n = Math.max(1, tiles);
  const trayW = Math.min(view.w - 2 * MARGIN, n * tile.w + (n - 1) * GAP);
  // The whole tile within reach, and never higher than the middle (the model
  // lives there) nor lower than three quarters down.
  const reachBottom = reach.y + reach.h;
  const cy = Math.min(view.h * 0.75, Math.max(view.h * 0.5, reachBottom - tile.h / 2));
  const tray = { x: (view.w - trayW) / 2, y: cy - tile.h / 2, w: trayW, h: tile.h };

  // The column and the zone sit inside the reach box horizontally, and at
  // the height a hand rests, a little above the middle.
  const midY = Math.min(Math.max(view.h * 0.44, reach.y + SIDE_H / 2), reachBottom - SIDE_H / 2);
  // Most of each inside the reach box, not just its middle: the + and -
  // buttons sit at the column's two sides.
  const sideCx = Math.max(MARGIN + SIDE_W / 2, reach.x + SIDE_W / 2 - 16);
  const side = { x: sideCx - SIDE_W / 2, y: midY - SIDE_H / 2, w: SIDE_W, h: SIDE_H };
  const zoneCx = Math.min(view.w - MARGIN - ZONE.w / 2, reach.x + reach.w - ZONE.w / 2 + 16);
  const zoneCy = Math.min(Math.max(view.h * 0.42, reach.y + ZONE.h / 2), reachBottom - ZONE.h / 2);
  const zone = { x: zoneCx - ZONE.w / 2, y: zoneCy - ZONE.h / 2, w: ZONE.w, h: ZONE.h };
  return { tile, tray, side, zone, reach };
}

/** The centre of a rect. */
export const centre = (r: Rect): Pt => ({ x: r.x + r.w / 2, y: r.y + r.h / 2 });

/**
 * Ease the tray toward where it should be: `current` and `target` are tray
 * centre y in pixels; it moves at most `rate` of the gap per second, so it
 * drifts rather than jumps when a hand comes into view.
 */
export function easeToward(current: number, target: number, dt: number, rate = 2.5): number {
  return current + (target - current) * (1 - Math.exp(-rate * Math.max(0, dt)));
}
