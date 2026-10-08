// Demo mode: a synthetic hand (or two) that runs the real gesture pipeline,
// so the interaction can be seen without a camera. It walks through the same
// controls a person uses: hover a tile to pick it, pinch to grab, move to
// spin, push toward the camera to grow, two-hand scale, drop it on ATLAS.
//
// Pure (no DOM): synthHand() is also what the tests use to build landmarks.
// Positions are stage pixels, y down.
import type { Pt, Size } from "./gestures";

// One right hand in its own frame: wrist at the origin, y up, units of hand
// size (wrist to middle MCP = 1). Indexed like MediaPipe's 21 landmarks.
const OPEN: ReadonlyArray<readonly [number, number]> = [
  [0, 0],
  [0.25, 0.2], [0.45, 0.4], [0.6, 0.6], [0.72, 0.78],        // thumb
  [0.3, 0.95], [0.33, 1.35], [0.35, 1.6], [0.36, 1.8],       // index
  [0.05, 1.0], [0.05, 1.45], [0.05, 1.72], [0.05, 1.95],     // middle
  [-0.18, 0.95], [-0.2, 1.35], [-0.21, 1.58], [-0.22, 1.76], // ring
  [-0.38, 0.85], [-0.42, 1.15], [-0.44, 1.32], [-0.46, 1.48], // pinky
];
type V2 = [number, number];
const PINCH_AT: V2 = [0.5, 1.15];
/** Where the fingertip cursor sits, in hand units from the wrist: the index
 * tip of an open/pointing hand, and the pinch point of a closed pinch. */
export const TIP_AT: V2 = [0.36, 1.8];
const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;
const lerp2 = (p: V2, q: V2, t: number): V2 => [lerp(p[0], q[0], t), lerp(p[1], q[1], t)];

function curlFinger(pts: V2[], mcp: number, c: number): void {
  const m = pts[mcp];
  if (!m) return;
  const set = (i: number, q: V2, k: number): void => {
    const p = pts[i];
    if (p) pts[i] = lerp2(p, q, k);
  };
  set(mcp + 2, [m[0], m[1] + 0.3], c);
  set(mcp + 3, [m[0], m[1] + 0.12], c);
  set(mcp + 1, [m[0], m[1] + 0.3], c * 0.4);
}

export interface SynthOptions {
  x: number; y: number;   // wrist, px
  size: number;           // hand size, px
  roll?: number;          // radians, screen plane (counter-clockwise)
  yaw?: number;           // radians, about the hand's up axis
  pitch?: number;         // radians, about the hand's across axis
  pinch?: number;         // 0 open .. 1 thumb and index touching
  point?: boolean;        // index out, other fingers curled
  left?: boolean;         // mirror for a left hand
}

export function synthHand(o: SynthOptions): Pt[] {
  const pts: V2[] = OPEN.map((p) => [p[0], p[1]]);
  const pinch = o.pinch ?? 0;
  const set = (i: number, q: V2, k: number): void => {
    const p = pts[i];
    if (p) pts[i] = lerp2(p, q, k);
  };
  if (o.point) {
    curlFinger(pts, 9, 1); curlFinger(pts, 13, 1); curlFinger(pts, 17, 1);
    set(4, [0.15, 0.75], 1 - pinch);
    set(3, [0.3, 0.6], 1 - pinch);
  }
  if (pinch > 0) {
    set(8, PINCH_AT, pinch);
    set(7, [0.45, 1.4], pinch);
    set(4, PINCH_AT, pinch);
    set(3, [0.62, 0.85], pinch);
  }
  const { roll = 0, yaw = 0, pitch = 0 } = o;
  const cr = Math.cos(roll), sr = Math.sin(roll);
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  return pts.map(([lx0, ly]) => {
    const lx = o.left ? -lx0 : lx0;
    // pitch about x, then yaw about y, then roll about z (z toward viewer)
    let X = lx, Y = ly * cp, Z = ly * sp;
    [X, Z] = [X * cy + Z * sy, -X * sy + Z * cy];
    [X, Y] = [X * cr - Y * sr, X * sr + Y * cr];
    // MediaPipe z: smaller is nearer the camera, so toward-viewer is -z.
    return { x: o.x + o.size * X, y: o.y - o.size * Y, z: -o.size * Z };
  });
}

const ease = (t: number): number => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));
const seg = (t: number, a: number, b: number): number => ease((t - a) / (b - a));

/**
 * A hand whose pinch point (thumb-index midpoint, the cursor) is at (px, py)
 * for this amount of pinch. `left` mirrors it, so either hand can be shown.
 */
export function handWithPinchAt(px: number, py: number, size: number, pinch: number,
                                left = false): Pt[] {
  const probe = synthHand({ x: 0, y: 0, size, pinch, left });
  const a = probe[4], b = probe[8];
  const mx = a && b ? (a.x + b.x) / 2 : 0, my = a && b ? (a.y + b.y) / 2 : 0;
  return synthHand({ x: px - mx, y: py - my, size, pinch, left });
}

export interface DemoTargets {
  /** Centre of the tile the demo pulls out. */
  tile?: Pt | null;
  /** Top edge of the tray (stage y). */
  trayTop?: number | null;
  /** Where the model is drawn now, when there is one. */
  model?: Pt | null;
  /** Centre of the ATLAS drop zone. */
  zone?: Pt | null;
}

export interface DemoFrame { hands: Pt[][]; caption: string; t: number; step: number }

/** Seconds per loop. */
export const DEMO_LENGTH = 20;

/**
 * The scripted demo, as a function of time (seconds, looping): the one
 * gesture, pinch and slide. Pull a model out of the tray, slide it about
 * (it follows and turns), let go (it settles), pinch it again with the
 * other hand, and let go over ATLAS. Returns the raw hands for this instant,
 * a caption, and which coach step (1..3) it is showing. Positions come from
 * the layout, so the hand really reaches the tile, the model and the zone.
 */
export function demoFrame(tIn: number, view: Size, targets: DemoTargets = {}): DemoFrame {
  const t = ((tIn % DEMO_LENGTH) + DEMO_LENGTH) % DEMO_LENGTH;
  const s = Math.min(view.w, view.h) * 0.16;            // palm size
  const cx = view.w * 0.47, cy = view.h * 0.36;         // where it is brought to
  const tile = targets.tile ?? { x: view.w * 0.4, y: view.h * 0.68 };
  const trayTop = targets.trayTop ?? tile.y - 60;
  const zone = targets.zone ?? { x: view.w - 110, y: view.h * 0.42 };
  const model = targets.model ?? { x: cx, y: cy };
  const hands: Pt[][] = [];
  let caption = "";
  let step = 1;
  const hand = (p: Pt, pinch: number, left = false): void => {
    hands.push(handWithPinchAt(p.x, p.y, s, pinch, left));
  };

  if (t < 4.4) {                                        // 1. pinch a tile, slide it up
    caption = "Pinch a model in the tray and slide it up to bring it out";
    const k = seg(t, 0, 1.6);
    const at = { x: lerp(view.w * 0.6, tile.x, k), y: lerp(view.h * 0.3, tile.y, k) };
    const pinch = seg(t, 1.9, 2.25);
    const up = seg(t, 2.5, 4.2);
    hand({ x: lerp(at.x, cx, up), y: lerp(at.y, Math.min(cy, trayTop - 120), up) }, pinch);
  } else if (t < 9.5) {                                 // 2. slide it about: it turns
    step = 2;
    caption = "Still pinched: slide to move it; it turns like a ball under your fingers";
    const u = t - 4.4;
    const pinch = u < 4.6 ? 1 : 1 - seg(u, 4.6, 4.95);
    const k = Math.min(u, 4.6);
    hand({ x: cx + Math.sin(k * 1.2) * view.w * 0.1, y: cy + Math.sin(k * 0.8) * view.h * 0.05 }, pinch);
  } else if (t < 12) {                                  // 3. let go: it settles
    step = 2;
    caption = "Let go: it stays, and settles";
    const u = t - 9.5;
    hand({ x: lerp(cx + Math.sin(4.6 * 1.2) * view.w * 0.1, view.w * 0.3, seg(u, 0.3, 2.2)),
      y: lerp(cy, view.h * 0.5, seg(u, 0.3, 2.2)) }, 0);
  } else if (t < 18.5) {                                // 4. the other hand, over to ATLAS
    step = 3;
    caption = "Either hand: pinch it and let go over ATLAS to give it";
    const u = t - 12;
    const k = seg(u, 0, 1.6);
    const reach = { x: lerp(view.w * 0.62, model.x, k), y: lerp(view.h * 0.5, model.y, k) };
    // `model` is where it was left (the app stops updating it once held),
    // so the carry starts from the same place the reach ended.
    const pinch = u < 5.4 ? seg(u, 1.8, 2.15) : 1 - seg(u, 5.4, 5.75);
    const carry = seg(u, 2.4, 5.0);
    const at = { x: lerp(reach.x, zone.x, carry), y: lerp(reach.y, zone.y, carry) };
    if (u < 6.2) hand(at, pinch, true);
  } else {
    step = 3;
    caption = "Demo loops. Press D or the Demo button to stop.";
  }
  return { hands, caption, t, step };
}
