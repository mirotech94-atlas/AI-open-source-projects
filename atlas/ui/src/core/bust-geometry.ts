/**
 * The point cloud the humanoid is made of.
 *
 * Pure arithmetic, deliberately: no `three`, no DOM, no `Math.random`. The
 * stage renders whatever this returns, so the shape can be reasoned about and
 * tested without a WebGL context — and a seed always produces the same bust,
 * which means a change to the figure shows up as a diff here rather than as
 * "it looks different today".
 *
 * Coordinates are in ATLAS units: the head sits around y = 1, the shoulders
 * around y = 0, and the figure faces +z. Nothing is scaled for a viewport;
 * the camera does that.
 */

/** ~18k points: dense enough to read as a person, cheap enough for a laptop. */
export const BUST_POINTS = 18000;

/** Which part of the bust a point belongs to. The renderer colours by this —
 *  FACE is the warm amber core — so membership is decided once, here, rather
 *  than re-derived from coordinates at draw time where it could drift. */
export const PART = {
  HEAD: 0,
  FACE: 1,
  NECK: 2,
  SHOULDERS: 3,
} as const;

export type Part = (typeof PART)[keyof typeof PART];

/** The centre of the amber cluster, and the point the brain's edges run to. */
export const faceCentre: readonly [number, number, number] = [0, 1.0, 0.40];
export const headAnchor: readonly [number, number, number] = [0, 1.06, 0];

export interface BustGeometry {
  /** x, y, z per point. */
  positions: Float32Array;
  /** One `PART` per point. */
  parts: Uint8Array;
  /** A stable per-point offset in [0, 2π), so jitter and the speaking pulse
   *  look like a cloud breathing rather than every point moving as one. */
  phases: Float32Array;
  /** 0..1 per point: how much a key light from above-left, in front, falls
   *  on the surface there. Makes the nose, sockets and cheekbones read as
   *  form instead of a flat cloud. */
  shades: Float32Array;
  count: number;
}

/** mulberry32. Small, fast, and — the only property that matters here —
 *  identical on every machine, unlike `Math.random`. Exported so anything
 *  else that needs a deterministic clock-driven cadence (the face's blink
 *  schedule) uses the same generator instead of growing a second one. */
export function prng(seed: number): () => number {
  let a = seed >>> 0;
  return (): number => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A direction drawn evenly over the sphere. Sampling `theta` and `phi`
 *  uniformly instead would crowd the poles, and on a head that reads as a
 *  bright dot on the crown. */
function onSphere(rand: () => number): [number, number, number] {
  const u = rand() * 2 - 1;
  const angle = rand() * Math.PI * 2;
  const r = Math.sqrt(Math.max(0, 1 - u * u));
  return [r * Math.cos(angle), u, r * Math.sin(angle)];
}

// Exported so the drawn face/outline layer (`face.ts`) derives its geometry
// from the same numbers the particle cloud is built from, rather than
// carrying a second set that could drift from what is actually on stage.
export const HEAD_C: [number, number, number] = [0, 1.04, 0];
export const HEAD_R: [number, number, number] = [0.60, 0.74, 0.62];
export const NECK_TOP = 0.46;
export const NECK_BOTTOM = 0.18;
export const NECK_R = 0.19;
export const SHOULDER_HALF = 1.02;
export const SHOULDER_DEPTH = 0.34;

const smooth = (e0: number, e1: number, x: number): number => {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
};
const bump = (x: number, y: number, cx: number, cy: number, sx: number, sy: number): number =>
  Math.exp(-(((x - cx) / sx) ** 2 + ((y - cy) / sy) ** 2));

/**
 * The head, sculpted rather than spherical: a point on the unit sphere is
 * pushed onto a skull with a brow ridge, eye sockets, cheekbones, a nose, a
 * jaw that narrows to a chin and a fuller back. Every feature is a smooth
 * bump over the sphere direction, weighted by how far the point faces
 * forward, so the face reads from the front and the skull from the side.
 * `face.ts` lands its landmarks on the same numbers (HEAD_C/HEAD_R).
 */
export function sculptHead(dx: number, dy: number, dz: number): [number, number, number] {
  const front = Math.max(0, dz);
  let sx = HEAD_R[0];
  const sy = HEAD_R[1];
  let sz = HEAD_R[2];
  // Jaw and chin: the lower third narrows, and the back of the jaw tucks in.
  const jaw = smooth(-0.12, -0.95, dy);
  sx *= 1 - 0.30 * jaw;
  sz *= 1 - (dz < 0 ? 0.42 : 0.10) * jaw;
  // Cranium: fuller behind and above, a skull rather than an egg.
  if (dz < 0) sz *= 1 + 0.10 * smooth(-0.2, 0.6, dy);
  // A flatter face plane, with cheekbones just below the eyes.
  sz *= 1 - 0.07 * front * front * (1 - jaw);
  sx *= 1 + 0.05 * bump(Math.abs(dx), dy, 0.62, -0.05, 0.22, 0.2);
  const x = dx * sx;
  // A shorter lower face: the chin sat low enough to read as alien.
  const y = dy * sy * (1 - 0.1 * jaw);
  let z = dz * sz;
  // Relief on the front only: sockets in, brow and nose and lips out, chin forward.
  const ax = Math.abs(dx);
  const relief =
    - 0.11 * bump(ax, dy, 0.34, 0.07, 0.15, 0.1)          // eye sockets
    + 0.07 * bump(ax, dy, 0.30, 0.25, 0.30, 0.06)         // brow ridge
    + 0.15 * bump(dx, dy, 0, -0.10, 0.07, 0.19)           // nose bridge to tip
    + 0.05 * bump(ax, dy, 0.10, -0.26, 0.07, 0.05)        // nostril wings
    - 0.05 * bump(ax, dy, 0.45, -0.36, 0.14, 0.14)        // cheek hollows
    + 0.05 * bump(dx, dy, 0, -0.45, 0.20, 0.05)           // lips
    - 0.03 * bump(dx, dy, 0, -0.58, 0.16, 0.05)           // under the lip
    + 0.08 * bump(dx, dy, 0, -0.84, 0.20, 0.12);          // chin
  z += relief * front * front;
  return [HEAD_C[0] + x, HEAD_C[1] + y, HEAD_C[2] + z];
}

export function bustGeometry(seed = 1, count = BUST_POINTS): BustGeometry {
  const positions = new Float32Array(count * 3);
  const parts = new Uint8Array(count);
  const phases = new Float32Array(count);
  const shades = new Float32Array(count).fill(1);
  const rand = prng(seed);

  const head = Math.round(count * 0.46);
  const face = Math.round(count * 0.06);
  const neck = Math.round(count * 0.07);
  // The torso takes the remainder so the totals always add to `count`.
  const shoulders = count - head - face - neck;

  let i = 0;
  const put = (part: Part, x: number, y: number, z: number, shade = 1): void => {
    shades[i] = shade;
    positions[i * 3] = x;
    positions[i * 3 + 1] = y;
    positions[i * 3 + 2] = z;
    parts[i] = part;
    phases[i] = rand() * Math.PI * 2;
    i += 1;
  };

  // --- head: a sculpted shell. The underside of the jaw where the neck
  // enters is left open, so the neck reads as growing out of it.
  // The face gets more of the points than the back of the skull: that is
  // where the eye goes, and a feature drawn in few points does not read.
  const LIGHT: [number, number, number] = [-0.42, 0.55, 0.72];
  const ears = Math.round(head * 0.06);
  let placed = 0;
  while (placed < head - ears) {
    const [dx, dy, dz] = onSphere(rand);
    if (dy < -0.8 && dz < 0.45) continue;
    if (dz < 0 && rand() < 0.45) continue;
    const shell = 0.965 + rand() * 0.035;
    const [x, y, z] = sculptHead(dx * shell, dy * shell, dz * shell);
    // The surface normal, numerically: two nearby points on the sculpt.
    const e = 0.02;
    const a = sculptHead(dx + e, dy, dz);
    const b = sculptHead(dx, dy + e, dz);
    const ux = a[0] - x, uy = a[1] - y, uz = a[2] - z;
    const vx = b[0] - x, vy = b[1] - y, vz = b[2] - z;
    let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    const len = Math.hypot(nx, ny, nz) || 1;
    nx /= len; ny /= len; nz /= len;
    // Outward, whichever way the tangents happened to wind.
    if (nx * dx + ny * dy + nz * dz < 0) { nx = -nx; ny = -ny; nz = -nz; }
    const lit = Math.max(0, nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]) / Math.hypot(...LIGHT);
    put(PART.HEAD, x, y, z, 0.35 + 0.65 * lit);
    placed += 1;
  }
  // Contours: the lines a face is recognised by -- jaw, brows, eye rims,
  // nose, lips, cheekbones -- traced in dots on the sculpted surface and lit
  // brighter than the skin. A cloud alone reads as a ball with eyes; the
  // contours are what make it a face. Coordinates are sphere directions on
  // the front (dx across, dy up); each polyline is mirrored for the pair.
  const CURVES: Array<{ pts: Array<[number, number]>; mirror: boolean; w: number }> = [
    { pts: [[0.95, 0.02], [0.9, -0.3], [0.72, -0.58], [0.42, -0.82], [0, -0.92]], mirror: true, w: 3 },
    { pts: [[0.12, 0.22], [0.3, 0.29], [0.5, 0.23]], mirror: true, w: 1.4 },
    { pts: [[0, 0.16], [0, -0.05], [0.02, -0.2]], mirror: false, w: 1.2 },
    { pts: [[0.13, -0.24], [0.06, -0.29], [0, -0.3]], mirror: true, w: 0.8 },
    { pts: [[0.2, -0.43], [0.08, -0.4], [0, -0.415]], mirror: true, w: 1 },
    { pts: [[0.17, -0.45], [0.07, -0.5], [0, -0.51]], mirror: true, w: 0.9 },
    { pts: [[0.36, -0.1], [0.52, -0.12], [0.7, -0.06]], mirror: true, w: 1.1 },
  ];
  // Eye rims: an almond round each socket.
  for (const side of [-1, 1]) {
    const ring: Array<[number, number]> = [];
    for (let a = 0; a <= 16; a += 1) {
      const t = (a / 16) * Math.PI * 2;
      ring.push([side * (0.34 + 0.13 * Math.cos(t)), 0.07 + 0.055 * Math.sin(t)]);
    }
    CURVES.push({ pts: ring, mirror: false, w: 1.2 });
  }
  const along = (pts: Array<[number, number]>, u: number): [number, number] => {
    const f = u * (pts.length - 1);
    const j = Math.min(pts.length - 2, Math.floor(f));
    const r = f - j;
    const [a0, a1] = pts[j]!;
    const [b0, b1] = pts[j + 1]!;
    return [a0 + (b0 - a0) * r, a1 + (b1 - a1) * r];
  };
  const weight = CURVES.reduce((sum, c) => sum + c.w * (c.mirror ? 2 : 1), 0);
  const contourPoints = Math.round(count * 0.07);
  for (const c of CURVES) {
    const sides = c.mirror ? [-1, 1] : [1];
    for (const side of sides) {
      const n = Math.round((contourPoints * c.w) / weight);
      for (let q = 0; q < n; q += 1) {
        const [cx, cy] = along(c.pts, rand());
        const jx = side * cx + (rand() - 0.5) * 0.012;
        const jy = cy + (rand() - 0.5) * 0.012;
        const dz = Math.sqrt(Math.max(0.02, 1 - jx * jx - jy * jy));
        const [x, y, z] = sculptHead(jx * 1.006, jy * 1.006, dz * 1.006);
        if (i < count) put(PART.HEAD, x, y, z, 1.45);
      }
    }
  }

  // Ears: a small shell each side, level with the eyes to the nose. Nothing
  // says "head" rather than "egg" more cheaply.
  for (let n = 0; n < ears; n += 1) {
    const side = n % 2 ? 1 : -1;
    const [dx, dy, dz] = onSphere(rand);
    put(PART.HEAD,
        side * (HEAD_R[0] * 0.97 + Math.abs(dx) * 0.05),
        HEAD_C[1] - 0.03 + dy * 0.15,
        HEAD_C[2] - 0.03 + dz * 0.09,
        side < 0 ? 0.8 : 0.45);
  }

  // --- the amber light: two eyes and, behind them, the core they burn
  // from. Most of it sits in the eyes -- the approved avatar's amber gaze --
  // and the rest is a faint glow the brain's edges run to.
  const [ex, ey] = [HEAD_R[0] * 0.34, HEAD_C[1] + HEAD_R[1] * 0.06];
  const eyeZ = HEAD_C[2] + HEAD_R[2] * 0.86;
  for (let n = 0; n < face; n += 1) {
    const [dx, dy, dz] = onSphere(rand);
    if (n % 5 < 3) {
      const side = n % 2 ? 1 : -1;
      const r = 0.055 * Math.cbrt(rand());
      put(PART.FACE, side * ex + dx * r * 1.5, ey + dy * r * 0.8, eyeZ + dz * r * 0.5);
    } else {
      const r = 0.2 * Math.cbrt(rand());
      put(PART.FACE,
          faceCentre[0] + dx * r,
          faceCentre[1] + dy * r * 1.1,
          faceCentre[2] - 0.12 + dz * r * 0.6);
    }
  }

  // --- neck: an elliptical column leaning slightly forward, flaring where
  // it meets the shoulders, so it grows into the trapezius instead of
  // standing on it like a post.
  for (let n = 0; n < neck; n += 1) {
    const t = rand();
    const y = NECK_BOTTOM + 0.08 + t * (NECK_TOP - NECK_BOTTOM);
    const angle = rand() * Math.PI * 2;
    const flare = 1 + 0.7 * smooth(0.4, 0, t);
    const r = NECK_R * 1.18 * flare * (0.97 + rand() * 0.03);
    put(PART.NECK, Math.cos(angle) * r,
        y, Math.sin(angle) * r * 0.82 + 0.05 * t);
  }

  // --- torso: traced from a silhouette, not a slab. The half-width at each
  // height is the outline a person has -- the trapezius falling away from
  // the neck, the deltoid rounding over as a quarter circle, the arm
  // dropping almost straight -- and the chest bows forward inside it. The
  // points thin toward the bottom of the frame, so the figure is a portrait
  // that fades rather than a statue cut off square.
  const TOP = NECK_BOTTOM + 0.14;
  const halfWidth = (y: number): number => {
    if (y >= 0.10) return 0.24 + ((TOP - y) / (TOP - 0.10)) * 0.56;
    if (y >= -0.08) return 0.80 + Math.sqrt(Math.max(0, 0.18 ** 2 - (y + 0.08) ** 2));
    return 0.98 - 0.08 * smooth(-0.08, -0.9, y);
  };
  for (let n = 0; n < shoulders && i < count; n += 1) {
    const y = TOP - Math.pow(rand(), 1.5) * (TOP + 0.9);
    const hw = halfWidth(y) * SHOULDER_HALF / 1.02;
    const u = rand() * 2 - 1;
    const x = u * hw;
    const round = Math.pow(Math.max(0, 1 - u * u), 0.55);
    const depth = SHOULDER_DEPTH * (0.75 + 0.3 * smooth(0.2, -0.4, y));
    const back = rand() < 0.3;
    put(PART.SHOULDERS, x, y, back ? -round * depth * 0.8 : round * depth);
  }

  return { positions, parts, phases, shades, count };
}
