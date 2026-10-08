// Demo mode: a synthetic hand (or two) that runs the real gesture pipeline,
// so the interaction can be seen without a camera.
//
// Pure (no DOM): synthHand() is also what the tests use to build landmarks.
// Positions are viewport pixels, y down.

// One right hand in its own frame: wrist at the origin, y up, units of hand
// size (wrist to middle MCP = 1). Indexed like MediaPipe's 21 landmarks.
const OPEN = [
  [0, 0],
  [0.25, 0.2], [0.45, 0.4], [0.6, 0.6], [0.72, 0.78],       // thumb
  [0.3, 0.95], [0.33, 1.35], [0.35, 1.6], [0.36, 1.8],      // index
  [0.05, 1.0], [0.05, 1.45], [0.05, 1.72], [0.05, 1.95],    // middle
  [-0.18, 0.95], [-0.2, 1.35], [-0.21, 1.58], [-0.22, 1.76], // ring
  [-0.38, 0.85], [-0.42, 1.15], [-0.44, 1.32], [-0.46, 1.48], // pinky
];
const PINCH_AT = [0.5, 1.15];
const lerp = (a, b, t) => a + (b - a) * t;
const lerp2 = (p, q, t) => [lerp(p[0], q[0], t), lerp(p[1], q[1], t)];

function curlFinger(pts, mcp, c) {
  // DIP and tip fold back toward the palm, just past the MCP.
  const m = pts[mcp];
  pts[mcp + 2] = lerp2(pts[mcp + 2], [m[0], m[1] + 0.3], c);
  pts[mcp + 3] = lerp2(pts[mcp + 3], [m[0], m[1] + 0.12], c);
  pts[mcp + 1] = lerp2(pts[mcp + 1], [m[0], m[1] + 0.3], c * 0.4);
}

/**
 * @param {object} o
 * @param {number} o.x,o.y   wrist position, px
 * @param {number} o.size    hand size, px
 * @param {number} [o.roll]  radians, in the screen plane (counter-clockwise)
 * @param {number} [o.yaw]   radians, about the hand's up axis
 * @param {number} [o.pitch] radians, about the hand's across axis
 * @param {number} [o.pinch] 0 open .. 1 thumb and index touching
 * @param {boolean} [o.point] index out, other fingers curled
 * @param {boolean} [o.left]  mirror for a left hand
 */
export function synthHand(o) {
  const pts = OPEN.map((p) => [...p]);
  const pinch = o.pinch ?? 0;
  if (o.point) {
    curlFinger(pts, 9, 1); curlFinger(pts, 13, 1); curlFinger(pts, 17, 1);
    pts[4] = lerp2(pts[4], [0.15, 0.75], 1 - pinch);
    pts[3] = lerp2(pts[3], [0.3, 0.6], 1 - pinch);
  }
  if (pinch > 0) {
    pts[8] = lerp2(pts[8], PINCH_AT, pinch);
    pts[7] = lerp2(pts[7], [0.45, 1.4], pinch);
    pts[4] = lerp2(pts[4], PINCH_AT, pinch);
    pts[3] = lerp2(pts[3], [0.62, 0.85], pinch);
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

const ease = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));
const seg = (t, a, b) => ease((t - a) / (b - a));

/**
 * The scripted demo, as a function of time (seconds, looping). Returns the
 * raw hands for this instant plus a caption. Positions are laid out from the
 * viewport size and the object's current screen position, so the synthetic
 * hand actually reaches the hologram and the PROPS list.
 *
 * @param {number} t
 * @param {{w:number,h:number}} view
 * @param {{propPoint?: {x:number,y:number}}} targets  menu item to pick
 */
export const DEMO_LENGTH = 26;
export function demoFrame(tIn, view, targets = {}) {
  const t = tIn % DEMO_LENGTH;
  const s = Math.min(view.w, view.h) * 0.16;          // hand size
  const cx = view.w * 0.42, cy = view.h * 0.55;
  // Pinch point of a right hand at roll 0 sits at (0.5, 1.15) hand units from
  // its wrist; place wrists so pinch points land where we want them.
  const wristFor = (px, py, size = s) => ({ x: px - 0.5 * size, y: py + 1.15 * size });
  const hands = [];
  let caption = '';

  if (t < 3) {                                        // 1. reach in, open hand
    caption = 'Demo: a synthetic hand reaches for the hologram';
    const k = seg(t, 0, 2.2);
    const w = wristFor(lerp(view.w * 0.1, cx, k), lerp(view.h * 0.95, cy, k));
    hands.push(synthHand({ ...w, size: s, point: true, pinch: seg(t, 2.2, 2.9) }));
  } else if (t < 8) {                                 // 2. grab, move, rotate
    caption = 'Pinch = grab · move the hand to move it · turn the wrist to rotate';
    const u = t - 3;
    const px = cx + Math.sin(u * 1.1) * view.w * 0.08;
    const py = cy - Math.sin(u * 0.8) * view.h * 0.06;
    const w = wristFor(px, py);
    hands.push(synthHand({
      ...w, size: s, pinch: 1 - seg(u, 4.5, 4.9), roll: Math.sin(u * 1.3) * 0.5,
      yaw: Math.sin(u * 0.9) * 0.5, pitch: Math.sin(u * 0.7) * 0.3,
    }));
  } else if (t < 14) {                                // 3. two hands: scale + turn
    caption = 'Two-hand pinch = scale by the distance, rotate by the angle';
    const u = t - 8;
    const spread = view.w * (0.15 + 0.045 * Math.sin(u * 1.2 - Math.PI / 2));
    const ang = Math.sin(u * 0.9) * 0.45;
    const join = seg(u, 0, 0.8);
    const ax = cx - Math.cos(ang) * spread, ay = cy - Math.sin(ang) * spread;
    const bx = cx + Math.cos(ang) * spread, by = cy + Math.sin(ang) * spread;
    const wa = wristFor(ax, ay);
    hands.push(synthHand({ ...wa, size: s, pinch: u < 5.6 ? seg(u, 0.2, 0.5) : 0 }));
    // left hand: mirrored, its pinch point is at -0.5 across
    hands.push(synthHand({
      x: bx + 0.5 * s, y: by + 1.15 * s, size: s, left: true,
      pinch: u < 5.4 ? join : 0,
    }));
  } else if (t < 19) {                                // 4. open palm, pick a prop
    const u = t - 14;
    const target = targets.propPoint ?? { x: view.w - 120, y: view.h * 0.4 };
    if (u < 1.6) {
      caption = 'Open palm held 1 s = menu';
      const w = wristFor(view.w * 0.55, view.h * 0.55);
      hands.push(synthHand({ ...w, size: s, pinch: 0 }));
    } else {
      caption = 'Point and pinch on a prop to choose it';
      const k = seg(u, 1.8, 3.4);
      const px = lerp(view.w * 0.55, target.x, k), py = lerp(view.h * 0.55, target.y, k);
      const w = wristFor(px, py);
      hands.push(synthHand({ ...w, size: s, point: true, pinch: seg(u, 3.6, 3.9) * (u < 4.5 ? 1 : 0) }));
    }
  } else if (t < 24) {                                // 5. grab and toss right
    caption = 'Flick and let go = toss · ATLAS catches it on the right';
    const u = t - 19;
    const k = seg(u, 0.2, 1.2);
    let px = lerp(view.w * 0.6, cx, k), py = cy;
    let pinch = seg(u, 1.2, 1.6);
    if (u > 2.0) {                                    // wind-up, then flick
      const f = (u - 2.0);
      px = cx - (f < 0.6 ? Math.sin(f / 0.6 * Math.PI / 2) * view.w * 0.06 : 0);
      if (f >= 0.6) px = cx - view.w * 0.06 + (f - 0.6) * view.w * 1.6;
      if (f > 0.78) pinch = 0;
    }
    if (u < 3.4) hands.push(synthHand({ ...wristFor(px, py), size: s, point: true, pinch }));
  } else {
    caption = 'Demo loops. Press D or the DEMO toggle to stop.';
  }
  return { hands, caption, t };
}
