import * as THREE from "three";
import { prng } from "./bust-geometry";

/**
 * CABLED ATLAS: a dark gunmetal android head, a glowing plexus web
 * over the face, on a neck of cables between angular shoulder plates.
 *
 * The head is its own long, narrow skull (`androidSurface`), cut with
 * angular facial planes -- brow ridge, deep almond sockets, a long nose,
 * high cheekbones over hollows, thin closed lips, a square chin -- in
 * satin blue-grey gunmetal, lit from behind so the silhouette reads on a
 * dark stage. A low-poly web of cyan lines and nodes lies over the face,
 * densest across the brow, round the eyes and over the cheeks. Engraved panel seams, rivets, gear stacks where the ears
 * would be, a slot in the crown, pistons behind the jaw, and the neck -- a
 * bundle of dark cables and pistons round a steel spine, dropping between
 * bevelled shoulder plates -- are the machine.
 *
 * Pure construction: nothing here animates. `Bust.tsx` owns the clock and
 * drives the handles this returns (eye glow, lid scale, the lip seam).
 * Coordinates are ATLAS units, the same as `bust-geometry.ts`.
 */

export interface Android {
  group: THREE.Group;
  /** Each eye's lens group: scale.y is the lid (1 open, ~0 shut). */
  eyes: THREE.Group[];
  /** Iris materials: emissiveIntensity is the glow. */
  irises: THREE.MeshStandardMaterial[];
  /** The soft bloom sprites round each eye: opacity follows the glow. */
  blooms: THREE.Sprite[];
  /** The plexus web's lines and nodes: opacity follows the eye glow. */
  plexusLines: THREE.LineBasicMaterial;
  plexusNodes: THREE.PointsMaterial;
  /** A soft cyan haze across the eye band: opacity follows the glow. */
  faceGlow: THREE.SpriteMaterial;
  /** The seam between the lips: its emissive lights with the voice. */
  lipSeam: THREE.MeshStandardMaterial;
  /** Every geometry/material/texture made here, for disposal. */
  dispose: () => void;
}

/** The skin: dark satin gunmetal with a blue-grey cast, not a mirror. */
const GUNMETAL = { color: 0x46525f, metalness: 0.85, roughness: 0.4 };
/** A lighter machined steel for the few trims that catch light. */
const STEEL = { color: 0x5d6976, metalness: 0.9, roughness: 0.32 };
const DARK_METAL = { color: 0x1c2228, metalness: 0.8, roughness: 0.45 };
/** Panel seams glow faintly, the reference's lit plate edges. */
const SEAM = { color: 0x0b0f13, metalness: 0.5, roughness: 0.6,
  emissive: new THREE.Color(0x2bb8f0), emissiveIntensity: 0.14 };

const sm = (e0: number, e1: number, x: number): number => {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
};
const g2 = (x: number, y: number, cx: number, cy: number, sx: number, sy: number): number =>
  Math.exp(-(((x - cx) / sx) ** 2 + ((y - cy) / sy) ** 2));
/** A hard-edged plane bump: flat top, linear falloff. Reads as a machined
 *  facet with a crease at its edge rather than a soft swelling. */
const tent = (d: number, w: number): number => Math.max(0, 1 - Math.abs(d) / w);
/** 1 inside [a, b], easing to 0 over `e` outside it. */
const band = (x: number, a: number, b: number, e: number): number =>
  sm(a - e, a, x) * (1 - sm(b, b + e, x));
/** Smoothstep interpolation through [h, value] keys sorted by h ascending. */
const keyed = (h: number, keys: ReadonlyArray<readonly [number, number]>): number => {
  if (h <= keys[0]![0]) return keys[0]![1];
  for (let i = 1; i < keys.length; i += 1) {
    const [h1, v1] = keys[i]!;
    if (h <= h1) {
      const [h0, v0] = keys[i - 1]!;
      return v0 + (v1 - v0) * sm(h0, h1, h);
    }
  }
  return keys[keys.length - 1]![1];
};

/** The android's own skull, in ATLAS units. Not HEAD_C/HEAD_R: the particle
 *  bust keeps those (and their tests); this head is longer and narrower --
 *  1.44 tall by 1.0 wide, the reference's human proportion. */
export const ANDROID_C: readonly [number, number, number] = [0, 1.12, 0];
/** Half-height above and below the centre. The eyes sit at ~0.47 of the
 *  height from the crown, as a person's do. */
const TOP = 0.7;
const BOTTOM = 0.74;
/** Half-width through the skull (h = 0.25 on the key scale): nothing
 *  outside the face's features is wider. */
const W_KEYS = [
  [-1, 0.17], [-0.9, 0.24], [-0.72, 0.33], [-0.48, 0.42], [-0.2, 0.47],
  [0.05, 0.49], [0.4, 0.49], [1, 0.48],
] as const;
/** Front depth: a flat face plane, the forehead set a touch back. */
const F_KEYS = [
  [-1, 0.46], [-0.85, 0.5], [-0.5, 0.5], [0, 0.52], [0.45, 0.5], [1, 0.44],
] as const;
/** Back depth: a full cranium, the back of the jaw tucked in hard above
 *  the neck. */
const B_KEYS = [
  [-1, 0.12], [-0.75, 0.22], [-0.45, 0.36], [-0.1, 0.52], [0.2, 0.6], [1, 0.58],
] as const;
/** Side of the head where the ear gear sits: level with the nose top. */
export const EAR_H = -0.04;

/**
 * The android head for a direction (dx, dy, dz). A lofted solid, not a
 * pushed sphere: latitude picks a height, the horizontal angle goes round a
 * superellipse cross-section whose half-width, front depth and back depth
 * are keyed by that height. Superellipse exponents under 1 flatten the
 * sides and the crown, so it reads as a machined skull rather than an egg.
 * The face is then cut into the front with plane-like bumps -- brow ridge,
 * deep almond sockets, a long straight nose, high cheekbones with hollows
 * beneath, closed thin lips, a square chin.
 *
 * `(dx, dy)` of the unit direction also serve as the face's own coordinates
 * (across, up) for placing features, which is what `onFace` passes in. A
 * non-unit direction scales the result out from the centre, so seams can be
 * lifted just proud of the surface.
 */
export function androidSurface(dx: number, dy: number, dz: number): [number, number, number] {
  const len = Math.hypot(dx, dy, dz) || 1;
  const ux = dx / len, uy = dy / len, uz = dz / len;
  const h = uy;
  const c = Math.sqrt(Math.max(0, 1 - h * h));
  // Flatter crown than a sphere; a nearly flat under-jaw where the neck enters.
  const s = Math.pow(c, h > 0 ? 0.36 : 0.36);
  const y = h > 0 ? TOP * Math.pow(h, 1.08) : -BOTTOM * Math.pow(-h, 1.25);
  const phi = Math.atan2(ux, uz);
  const sp = Math.sin(phi), cp = Math.cos(phi);
  const e = 0.78;
  const W = keyed(h, W_KEYS);
  const D = cp >= 0 ? keyed(h, F_KEYS) : keyed(h, B_KEYS);
  let x = s * W * Math.sign(sp) * Math.pow(Math.abs(sp), e);
  let z = s * D * Math.sign(cp) * Math.pow(Math.abs(cp), e);

  // --- the face, on the front only -----------------------------------
  const fw = sm(0.1, 0.55, uz);
  const X = ux, Y = uy, ax = Math.abs(ux);
  const side = ux < 0 ? -1 : 1;
  let dz2 = 0, dx2 = 0;
  // Brow ridge: a hard shelf across above the eyes, dipping at the bridge,
  // sharp underneath, easing into the forehead above.
  const browY = 0.2 + 0.07 * sm(0.05, 0.4, ax) - 0.03 * sm(0.45, 0.7, ax);
  const under = Y < browY ? 0.045 : 0.14;
  dz2 += 0.03 * Math.max(0, 1 - Math.abs(Y - browY) / under) * band(ax, -0.1, 0.6, 0.12);
  // Deep almond sockets.
  dz2 -= 0.075 * g2(ax, Y, 0.4, 0.08, 0.2, 0.1);
  // Long straight nose: bridge from between the brows, rising to a tip,
  // then cut back hard to the upper lip.
  const noseRise = sm(0.16, -0.3, Y) * (Y > -0.34 ? 1 : Math.max(0, 1 - (-0.34 - Y) / 0.06));
  dz2 += 0.12 * noseRise * tent(X, 0.07 + 0.045 * sm(-0.05, -0.32, Y));
  // Nostril wings.
  dz2 += 0.035 * g2(ax, Y, 0.1, -0.31, 0.05, 0.04);
  // Cheekbones: a high, sharp ridge from under the outer eye out to the
  // temple -- a facet edge, not a swelling.
  const ridgeY = -0.1 - 0.22 * (0.75 - ax);
  const cheek = Math.max(0, 1 - Math.abs(Y - ridgeY) / (Y > ridgeY ? 0.14 : 0.06))
    * band(ax, 0.32, 0.78, 0.1);
  dz2 += 0.05 * cheek;
  dx2 += side * 0.025 * cheek * (1 - fw * 0.5);
  // The hollow plane under each cheekbone.
  const hollow = g2(ax, Y, 0.56, -0.42, 0.17, 0.15);
  dz2 -= 0.06 * hollow;
  dx2 -= side * 0.035 * hollow;
  // Muzzle: the mouth area stands out of the hollows.
  dz2 += 0.015 * g2(X, Y, 0, -0.58, 0.26, 0.14);
  // Thin closed lips: a narrow upper and lower lip.
  dz2 += 0.014 * g2(X, Y, 0, -0.55, 0.17, 0.028) + 0.016 * g2(X, Y, 0, -0.612, 0.14, 0.03);
  dz2 -= 0.012 * g2(X, Y, 0, -0.665, 0.14, 0.025);
  // Philtrum.
  dz2 -= 0.01 * g2(X, Y, 0, -0.44, 0.025, 0.05);
  // A square chin: a flat-fronted block, forward of the jaw.
  dz2 += 0.03 * band(ax, -0.2, 0.15, 0.12) * band(Y, -0.97, -0.78, 0.12);
  // Jaw corner: a crisp step out at the back of the jaw line.
  dx2 += side * 0.025 * g2(ax, Y, 0.8, -0.52, 0.14, 0.1) * sm(0.1, 0.5, uz);
  x += dx2 * sm(-0.35, 0.3, uz);
  z += dz2 * fw;

  return [ANDROID_C[0] + x * len, ANDROID_C[1] + y * len, ANDROID_C[2] + z * len];
}

/** A point on the android's face for face coordinates (across, up) on the
 *  front, pushed `lift` out from the centre (seams sit just proud of it). */
function onFace(dx: number, dy: number, lift = 1.004): THREE.Vector3 {
  const dz = Math.sqrt(Math.max(0.02, 1 - dx * dx - dy * dy));
  const [x, y, z] = androidSurface(dx * lift, dy * lift, dz * lift);
  return new THREE.Vector3(x, y, z);
}

export function buildAndroid(): Android {
  const group = new THREE.Group();
  const owned: Array<{ dispose: () => void }> = [];
  const mat = (p: THREE.MeshStandardMaterialParameters): THREE.MeshStandardMaterial => {
    const m = new THREE.MeshStandardMaterial(p);
    owned.push(m);
    return m;
  };
  const geo = <G extends THREE.BufferGeometry>(g: G): G => {
    owned.push(g);
    return g;
  };

  const skin = mat(GUNMETAL);
  const steel = mat(STEEL);
  const darkMetal = mat(DARK_METAL);
  const seamMat = mat(SEAM);

  // --- the head: a dense sphere pushed onto the sculpt ------------------
  const head = geo(new THREE.SphereGeometry(1, 220, 180));
  const pos = head.attributes["position"] as THREE.BufferAttribute;
  const v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i += 1) {
    v.fromBufferAttribute(pos, i).normalize();
    const [x, y, z] = androidSurface(v.x, v.y, v.z);
    pos.setXYZ(i, x, y, z);
  }
  head.computeVertexNormals();
  group.add(new THREE.Mesh(head, skin));

  // --- seams: engraved panel lines, as fine dark tubes on the surface ---
  const seam = (pts: THREE.Vector3[], radius = 0.0042, material = seamMat): void => {
    if (pts.length < 2) return;
    const curve = new THREE.CatmullRomCurve3(pts, false, "catmullrom", 0.1);
    group.add(new THREE.Mesh(geo(new THREE.TubeGeometry(curve, pts.length * 16, radius, 6)), material));
  };
  const line = (pts: Array<[number, number]>, s: number, r?: number, lift?: number): void =>
    seam(pts.map(([x, y]) => onFace(s * x, y, lift)), r);
  const rivet = (x: number, y: number): void => {
    const m = new THREE.Mesh(geo(new THREE.SphereGeometry(0.01, 10, 8)), darkMetal);
    m.position.copy(onFace(x, y, 1.0));
    group.add(m);
  };
  /** A grid laid on the surface: `at(u, v)` gives the surface direction
   *  for u, v in 0..1. Used for inlaid dark panels the head shows its
   *  machinery through, which a tube or a sphere stuck on cannot do. */
  const patch = (at: (u: number, v: number) => [number, number, number], material: THREE.Material,
    nu = 24, nv = 12): void => {
    const g = new THREE.BufferGeometry();
    const verts: number[] = [];
    const index: number[] = [];
    for (let j = 0; j <= nv; j += 1) {
      for (let i = 0; i <= nu; i += 1) {
        verts.push(...androidSurface(...at(i / nu, j / nv)));
      }
    }
    for (let j = 0; j < nv; j += 1) {
      for (let i = 0; i < nu; i += 1) {
        const a = j * (nu + 1) + i, b = a + 1, c = a + nu + 1, d = c + 1;
        index.push(a, c, b, b, c, d);
      }
    }
    g.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
    g.setIndex(index);
    g.computeVertexNormals();
    const m = new THREE.Mesh(geo(g), material);
    group.add(m);
  };
  /** An elliptical dark recess on the face, centred at face coords. */
  const recess = (cx: number, cy: number, rx: number, ry: number, material: THREE.Material): void =>
    patch((u, v) => {
      const a = u * Math.PI * 2;
      const x = cx + Math.cos(a) * rx * v, y = cy + Math.sin(a) * ry * v;
      const z = Math.sqrt(Math.max(0.02, 1 - x * x - y * y));
      return [x * 1.004, y * 1.004, z * 1.004];
    }, material, 32, 6);
  const shadowMat = mat({ color: 0x0b0e11, metalness: 0.5, roughness: 0.7, side: THREE.DoubleSide });
  for (const s of [-1, 1]) {
    // The forehead plate: up each side of the crown channel, then a
    // stepped line out across the brow to the temple -- the reference's
    // angular brow plates.
    line([[0.26, 0.86], [0.28, 0.74], [0.26, 0.6], [0.3, 0.46], [0.46, 0.44], [0.58, 0.5], [0.74, 0.46]], s);
    // Brow plate edge: from the bridge, over the socket, down the temple.
    line([[0.06, 0.26], [0.16, 0.33], [0.3, 0.34], [0.44, 0.3], [0.56, 0.32], [0.7, 0.26], [0.8, 0.12]], s);
    // Under the eye: the lower lid plate out to the cheekbone.
    line([[0.2, -0.02], [0.32, -0.06], [0.48, -0.05], [0.62, 0.0]], s, 0.0036);
    // The cheekbone's lower edge: temple down to the hollow.
    line([[0.8, -0.04], [0.68, -0.2], [0.56, -0.34]], s);
    // Nose wing past the mouth corner to the jaw -- the long cheek line.
    line([[0.14, -0.3], [0.22, -0.42], [0.27, -0.56], [0.3, -0.7], [0.34, -0.8]], s);
    // Jaw plate: from under the ear along the jaw to the chin.
    line([[0.86, -0.36], [0.74, -0.56], [0.52, -0.74], [0.3, -0.86], [0.18, -0.9]], s);
    // Chin block edges.
    line([[0.15, -0.72], [0.16, -0.86]], s, 0.0036);
    // Rivets: forehead, temples, cheeks by the mouth, the chin.
    for (const [x, y] of [[0.28, 0.54], [0.36, 0.52], [0.66, 0.36], [0.36, -0.6], [0.38, -0.66], [0.22, -0.84]] as const) {
      rivet(s * x, y);
    }
    // The hollow under the cheekbone opens on the mechanism: a dark cavity
    // at the side of the face, a cog and a piston showing through.
    // the face plate's edge opens on the mechanism at the side of the jaw:
    // an outlined recess with a cog and a piston showing through.
    recess(s * 0.68, -0.46, 0.07, 0.15, shadowMat);
    line([[0.64, -0.3], [0.72, -0.36], [0.75, -0.48], [0.71, -0.6], [0.63, -0.58], [0.6, -0.44], [0.64, -0.3]], s, 0.006);
    const hollowAt = onFace(s * 0.68, -0.45, 1.005);
    const cog = new THREE.Mesh(geo(new THREE.CylinderGeometry(0.026, 0.026, 0.016, 10)), darkMetal);
    cog.rotation.z = Math.PI / 2;
    cog.rotation.y = -s * 0.9;
    cog.position.copy(hollowAt).add(new THREE.Vector3(0, 0.035, 0));
    const rod = new THREE.Mesh(geo(new THREE.CylinderGeometry(0.008, 0.008, 0.1, 8)), steel);
    rod.position.copy(hollowAt).add(new THREE.Vector3(0, -0.03, 0));
    rod.rotation.z = s * 0.2;
    group.add(cog, rod);
  }
  // The split between the thin lips, rising slightly at the corners into
  // the reference's faint smile. Its own material, so the voice can light it.
  const lipSeam = mat({ ...SEAM, emissive: new THREE.Color(0x3fd0ff), emissiveIntensity: 0 });
  seam([[-0.17, -0.565], [-0.12, -0.578], [-0.06, -0.585], [0, -0.583], [0.06, -0.585], [0.12, -0.578], [0.17, -0.565]]
    .map(([x, y]) => onFace(x!, y!, 1.006)), 0.0055, lipSeam);

  // --- eyes: deep almond sockets, a lid plate, and a glowing ring iris ---
  const bloomTexture = (() => {
    const c = document.createElement("canvas");
    c.width = c.height = 128;
    const g = c.getContext("2d");
    if (g) {
      const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
      grad.addColorStop(0, "rgba(174,243,255,1)");
      grad.addColorStop(0.25, "rgba(63,208,255,.55)");
      grad.addColorStop(1, "rgba(63,208,255,0)");
      g.fillStyle = grad;
      g.fillRect(0, 0, 128, 128);
    }
    const tex = new THREE.CanvasTexture(c);
    owned.push(tex);
    return tex;
  })();
  const eyes: THREE.Group[] = [];
  const irises: THREE.MeshStandardMaterial[] = [];
  const blooms: THREE.Sprite[] = [];
  for (const s of [-1, 1]) {
    const at = onFace(s * 0.4, 0.07, 0.99);
    // The socket: a dark almond recess, wider than tall, tipped up at the
    // outer corner, under a gunmetal lid plate.
    const socket = new THREE.Mesh(geo(new THREE.SphereGeometry(0.08, 28, 18)),
      mat({ color: 0x0a0d10, metalness: 0.6, roughness: 0.5 }));
    socket.scale.set(1.6, 0.66, 0.35);
    socket.rotation.z = s * 0.14;
    socket.position.copy(at);
    group.add(socket);
    const lid = new THREE.Mesh(geo(new THREE.TorusGeometry(0.13, 0.012, 8, 28, Math.PI)), skin);
    lid.scale.set(1.02, 0.42, 1);
    lid.rotation.z = s * 0.14;
    lid.position.copy(at).add(new THREE.Vector3(0, 0.004, 0.018));
    group.add(lid);
    const lens = new THREE.Group();
    lens.position.copy(at).add(new THREE.Vector3(0, 0, 0.022));
    lens.rotation.z = s * 0.14;
    // The eye itself: a glowing white-cyan almond slit. Tone mapping off,
    // so a bright glow stays white-cyan rather than greying to teal.
    const iris = mat({ color: 0x000000, emissive: new THREE.Color(0xcff6ff), emissiveIntensity: 1.2,
      metalness: 0, roughness: 0.4, toneMapped: false });
    const slit = new THREE.Mesh(geo(new THREE.SphereGeometry(0.1, 32, 12)), iris);
    slit.scale.set(0.95, 0.25, 0.22);
    lens.add(slit);
    const spriteMat = new THREE.SpriteMaterial({ map: bloomTexture, color: 0x6fdcff, transparent: true,
      blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.55, toneMapped: false });
    owned.push(spriteMat);
    const bloom = new THREE.Sprite(spriteMat);
    bloom.scale.set(0.46, 0.2, 1);
    bloom.position.z = 0.03;
    lens.add(bloom);
    group.add(lens);
    eyes.push(lens);
    irises.push(iris);
    blooms.push(bloom);
  }

  // The web's light on the skin: a wide, soft haze across the eyes and
  // the bridge of the nose, where the plexus is densest.
  const faceGlow = new THREE.SpriteMaterial({ map: bloomTexture, color: 0x2fa8e8, transparent: true,
    blending: THREE.AdditiveBlending, depthWrite: false, depthTest: false, opacity: 0.2, toneMapped: false });
  owned.push(faceGlow);
  const haze = new THREE.Sprite(faceGlow);
  haze.scale.set(1.25, 0.42, 1);
  haze.position.copy(onFace(0, 0.08, 1.06));
  group.add(haze);

  // --- the crown slot: a channel from the top of the forehead back over
  // the skull, the mechanism showing inside -----------------------------
  // The slot runs from the upper forehead (t = 0) to just short of the
  // top of the crown (t = 1), so steel frames it all round from the front; x is across it, in face coordinates.
  const slotDir = (t: number, x: number, lift: number): [number, number, number] => {
    const a = 0.62 + t * 0.66;
    return [x * lift, Math.sin(a) * lift, Math.cos(a) * lift];
  };
  const crownAt = (t: number, x = 0, lift = 1): THREE.Vector3 =>
    new THREE.Vector3(...androidSurface(...slotDir(t, x, lift)));
  const slotPts = (x: number, lift: number): THREE.Vector3[] =>
    Array.from({ length: 9 }, (_, k) => crownAt(k / 8, x, lift));
  patch((u, v) => slotDir(v, (u - 0.5) * 0.22, 1.004), shadowMat, 8, 32);
  for (const s of [-1, 1]) {
    seam(slotPts(s * 0.115, 1.004), 0.011, steel);
  }
  seam([-0.115, -0.06, 0, 0.06, 0.115].map((x) => crownAt(0, x, 1.004)), 0.011, steel);
  seam([-0.115, -0.06, 0, 0.06, 0.115].map((x) => crownAt(1, x, 1.004)), 0.011, steel);
  for (const t of [0.2, 0.5, 0.8]) {
    const cog = new THREE.Mesh(geo(new THREE.CylinderGeometry(0.045, 0.045, 0.024, 12)),
      mat({ color: 0x5d6873, metalness: 0.9, roughness: 0.3, emissive: new THREE.Color(0x1f8fa8), emissiveIntensity: 0.9 }));
    cog.position.copy(crownAt(t, 0, 0.99));
    cog.rotation.z = Math.PI / 2;
    group.add(cog);
  }

  // --- ears: stacked discs and a gear, flush at each temple -------------
  for (const s of [-1, 1]) {
    const earDir = new THREE.Vector3(s, EAR_H, -0.08).normalize();
    const earAt = new THREE.Vector3(...androidSurface(earDir.x, earDir.y, earDir.z));
    const ear = new THREE.Group();
    ear.position.copy(earAt).add(new THREE.Vector3(-s * 0.02, 0, 0));
    ear.rotation.set(0, -s * 0.35, Math.PI / 2, "YXZ");
    const stack: Array<[number, number, THREE.MeshStandardMaterial]> = [
      [0.17, 0.04, darkMetal], [0.14, 0.04, skin], [0.1, 0.04, darkMetal], [0.055, 0.045, steel],
    ];
    stack.forEach(([r, hgt, m], idx) => {
      const disc = new THREE.Mesh(geo(new THREE.CylinderGeometry(r, r, hgt, 32)), m);
      disc.position.y = -s * (0.01 + idx * 0.022);
      ear.add(disc);
    });
    for (let t = 0; t < 16; t += 1) {
      const a = (t / 16) * Math.PI * 2;
      const tooth = new THREE.Mesh(geo(new THREE.BoxGeometry(0.024, 0.026, 0.02)), skin);
      tooth.position.set(Math.cos(a) * 0.178, -s * 0.01, Math.sin(a) * 0.178);
      tooth.rotation.y = -a;
      ear.add(tooth);
    }
    group.add(ear);

    // Pistons behind the jaw, running down toward the neck.
    for (const [dy, len] of [[0, 0.16], [0.06, 0.12]] as const) {
      const sleeve = new THREE.Mesh(geo(new THREE.CylinderGeometry(0.022, 0.022, len, 14)), darkMetal);
      sleeve.position.set(s * (0.3 - dy * 0.5), ANDROID_C[1] - 0.52 + dy, ANDROID_C[2] - 0.12);
      sleeve.rotation.z = s * 0.3;
      group.add(sleeve);
    }
  }

  // --- the neck: a bundle of cables and two pistons round a steel
  // spine, clearly narrower than the jaw --------------------------------
  const jawY = ANDROID_C[1] - BOTTOM * 0.72;
  const collarY = 0.02;
  const cableMat = mat({ color: 0x14191e, metalness: 0.7, roughness: 0.4 });
  const cables = 13;
  for (let c = 0; c < cables; c += 1) {
    const a = (c / cables) * Math.PI * 2 + 0.2;
    const r0 = 0.1 + (c % 3) * 0.012;
    const r1 = 0.17 + (c % 2) * 0.025;
    const wob = ((c * 37) % 7) / 7 - 0.5;
    const pts = [
      new THREE.Vector3(Math.cos(a) * r0, jawY + 0.08, Math.sin(a) * r0 * 0.85 - 0.04),
      new THREE.Vector3(Math.cos(a + wob * 0.4) * (r0 + 0.005), jawY - (jawY - collarY) * 0.35, Math.sin(a + wob * 0.4) * r0 * 0.9 - 0.02),
      new THREE.Vector3(Math.cos(a - wob * 0.3) * (r0 + 0.02), jawY - (jawY - collarY) * 0.7, Math.sin(a - wob * 0.3) * r0 * 0.95),
      new THREE.Vector3(Math.cos(a) * r1, collarY - 0.06, Math.sin(a) * r1 * 0.9),
    ];
    const radius = 0.016 + (c % 4) * 0.004;
    group.add(new THREE.Mesh(geo(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 40, radius, 10)),
      c % 4 === 0 ? darkMetal : cableMat));
  }
  const spine = new THREE.Mesh(geo(new THREE.TubeGeometry(new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, jawY + 0.05, 0.02), new THREE.Vector3(0, (jawY + collarY) / 2, 0.08),
    new THREE.Vector3(0, collarY, 0.13)]), 30, 0.016, 10)), steel);
  group.add(spine);
  // Pistons down each side of the throat: a dark sleeve and a steel rod.
  for (const s of [-1, 1]) {
    const top = new THREE.Vector3(s * 0.11, jawY - 0.02, 0.06);
    const bottom = new THREE.Vector3(s * 0.16, collarY - 0.02, 0.1);
    const mid = top.clone().lerp(bottom, 0.5);
    const dir = bottom.clone().sub(top);
    const tilt = Math.atan2(dir.x, -dir.y);
    const sleeve = new THREE.Mesh(geo(new THREE.CylinderGeometry(0.024, 0.024, dir.length() * 0.55, 14)), darkMetal);
    sleeve.position.copy(top.clone().lerp(bottom, 0.7));
    sleeve.rotation.z = tilt;
    const rod = new THREE.Mesh(geo(new THREE.CylinderGeometry(0.011, 0.011, dir.length() * 0.9, 10)), steel);
    rod.position.copy(mid);
    rod.rotation.z = tilt;
    group.add(sleeve, rod);
  }

  // --- shoulders: bevelled armour plates, sloping down and out from the
  // collar, with trapezius cables running up into the neck -------------
  const plate = (w0: number, w1: number, hgt: number, depth: number): THREE.ExtrudeGeometry => {
    // A trapezoid, wide at the base: the plate's face in its own x/y.
    const sh = new THREE.Shape();
    sh.moveTo(-w0 / 2, 0);
    sh.lineTo(w0 / 2, 0);
    sh.lineTo(w1 / 2, hgt);
    sh.lineTo(-w1 / 2, hgt);
    sh.closePath();
    const g = geo(new THREE.ExtrudeGeometry(sh, {
      depth, bevelEnabled: true, bevelThickness: 0.018, bevelSize: 0.016, bevelSegments: 2, curveSegments: 1,
    }));
    g.translate(0, -hgt / 2, -depth / 2);
    return g;
  };
  const armour = mat({ ...GUNMETAL, color: 0x323c47 });
  const collarPlate = new THREE.Mesh(plate(0.5, 0.36, 0.1, 0.36), armour);
  collarPlate.position.set(0, collarY - 0.1, 0.02);
  // The sternum: wide at the top, narrowing down into the chest.
  const sternum = new THREE.Mesh(plate(0.26, 0.46, 0.26, 0.18), armour);
  sternum.position.set(0, collarY - 0.33, 0.1);
  group.add(collarPlate, sternum);
  for (const s of [-1, 1]) {
    // [x, y, z, roll, width at base, width at top, height, depth]
    const plates: Array<[number, number, number, number, number, number, number, number]> = [
      [0.3, -0.08, 0.0, 0.2, 0.46, 0.42, 0.07, 0.4],     // trapezius
      [0.66, -0.17, -0.02, 0.34, 0.42, 0.36, 0.09, 0.46], // shoulder cap
      [0.88, -0.36, -0.02, 0.95, 0.3, 0.24, 0.08, 0.42],   // deltoid, dropping away
      [0.2, -0.19, 0.17, 0.14, 0.3, 0.26, 0.05, 0.12],    // clavicle, forward
    ];
    for (const [x, y, z, roll, w0, w1, h, d] of plates) {
      const m = new THREE.Mesh(plate(w0, w1, h, d), armour);
      m.position.set(s * x, collarY + y, z);
      m.rotation.set(0, 0, -s * roll);
      group.add(m);
    }
    // Trapezius cables: from the back of the neck, hugging it, then out
    // under the plates.
    for (const [ox, oz, r] of [[0.0, -0.06, 0.02], [0.04, -0.13, 0.017]] as const) {
      const pts = [
        new THREE.Vector3(s * 0.1, jawY - 0.02, oz),
        new THREE.Vector3(s * (0.14 + ox), jawY - 0.28, oz - 0.01),
        new THREE.Vector3(s * (0.22 + ox), collarY - 0.02, oz - 0.02),
        new THREE.Vector3(s * (0.36 + ox), collarY - 0.1, oz - 0.02),
      ];
      group.add(new THREE.Mesh(geo(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 24, r, 8)), cableMat));
    }
  }

  // --- the plexus: a low-poly web of cyan lines and nodes over the face --
  const plexus = buildPlexus(bloomTexture);
  owned.push(plexus.lines.geometry, plexus.nodes.geometry, plexus.lineMat, plexus.nodeMat);
  group.add(plexus.lines, plexus.nodes);

  return {
    group, eyes, irises, blooms, lipSeam,
    plexusLines: plexus.lineMat, plexusNodes: plexus.nodeMat, faceGlow,
    dispose: () => owned.forEach((o) => o.dispose()),
  };
}

/** Face coordinates of the parts the web must not cross: the eye slits,
 *  the lip line and the crown slot. */
function blocked(x: number, y: number): boolean {
  const ax = Math.abs(x);
  if (((ax - 0.4) / 0.16) ** 2 + ((y - 0.07) / 0.075) ** 2 < 1) return true;
  if (ax < 0.19 && Math.abs(y + 0.58) < 0.025) return true;
  return ax < 0.14 && y > 0.56;
}

/** How dense the web is at a face coordinate, 0..1: thickest over the
 *  brow, round the eyes and across the cheeks, thinning to the sides, the
 *  jaw and the chin, and gone round the back. */
function webDensity(x: number, y: number): number {
  const r2 = x * x + y * y;
  if (r2 >= 0.97) return 0;
  const dz = Math.sqrt(1 - r2);
  const ax = Math.abs(x);
  const front = sm(0.2, 0.72, dz);
  const hot = 0.3
    + 0.8 * g2(ax, y, 0.34, 0.1, 0.42, 0.2)     // a band round the eyes
    + 0.45 * g2(x, y, 0, 0.42, 0.42, 0.2)       // the forehead
    + 0.35 * g2(ax, y, 0.46, -0.22, 0.2, 0.18); // the cheeks
  const low = 1 - 0.65 * sm(-0.3, -0.85, y);   // thinner at the jaw and chin
  return Math.min(1, front * hot * low);
}

/**
 * The plexus. Nodes are sampled in the face's own (across, up) coordinates
 * by rejection against `webDensity`, deterministically from a fixed seed;
 * each is joined to its nearest neighbours in that 2D space (a k-nearest
 * graph, which reads as a triangulation at this density). Every edge is cut
 * into short pieces, each laid back onto `androidSurface` just proud of the
 * skin, so a line follows the nose and cheek rather than cutting through
 * them as a chord would; depth testing then hides the far side as the head
 * turns. Brightness per vertex follows the density, so the web glows most
 * where it is thickest.
 */
function buildPlexus(sprite: THREE.Texture): {
  lines: THREE.LineSegments; nodes: THREE.Points;
  lineMat: THREE.LineBasicMaterial; nodeMat: THREE.PointsMaterial;
} {
  const rand = prng(0xa71a5);
  const pts: Array<[number, number, number]> = [];
  for (let tries = 0; pts.length < 300 && tries < 40000; tries += 1) {
    const x = rand() * 2 - 1, y = rand() * 2 - 1;
    if (blocked(x, y)) continue;
    const d = webDensity(x, y);
    if (d <= 0 || rand() > d) continue;
    // Keep nodes a little apart, so the web reads as a mesh, not a smear.
    if (pts.some(([px, py]) => (px - x) ** 2 + (py - y) ** 2 < 0.0055)) continue;
    pts.push([x, y, d]);
  }
  const edges = new Set<string>();
  const K = 4, MAX = 0.26;
  pts.forEach(([x, y], i) => {
    pts.map(([px, py], j) => [j, (px - x) ** 2 + (py - y) ** 2] as const)
      .filter(([j, d2]) => j !== i && d2 < MAX * MAX)
      .sort((a, b) => a[1] - b[1])
      .slice(0, K)
      .forEach(([j]) => edges.add(i < j ? `${i}:${j}` : `${j}:${i}`));
  });
  const at = (x: number, y: number, lift: number): [number, number, number] => {
    const z = Math.sqrt(Math.max(0.02, 1 - x * x - y * y));
    return androidSurface(x * lift, y * lift, z * lift);
  };
  const base = new THREE.Color(0x3fd6ff);
  const linePos: number[] = [], lineCol: number[] = [];
  const PIECES = 3;
  for (const key of edges) {
    const [i, j] = key.split(":").map(Number) as [number, number];
    const [x0, y0, d0] = pts[i]!, [x1, y1, d1] = pts[j]!;
    // Crossing an eye or the mouth would draw a line over it: skip.
    if (blocked((x0 + x1) / 2, (y0 + y1) / 2)) continue;
    for (let k = 0; k < PIECES; k += 1) {
      for (const t of [k / PIECES, (k + 1) / PIECES]) {
        linePos.push(...at(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, 1.008));
        const d = d0 + (d1 - d0) * t;
        const b = 0.12 + 0.88 * d * d;
        lineCol.push(base.r * b, base.g * b, base.b * b);
      }
    }
  }
  const lineGeo = new THREE.BufferGeometry();
  lineGeo.setAttribute("position", new THREE.Float32BufferAttribute(linePos, 3));
  lineGeo.setAttribute("color", new THREE.Float32BufferAttribute(lineCol, 3));
  const lineMat = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.5,
    blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false });
  const nodeGeo = new THREE.BufferGeometry();
  nodeGeo.setAttribute("position", new THREE.Float32BufferAttribute(pts.flatMap(([x, y]) => at(x, y, 1.012)), 3));
  const nodeMat = new THREE.PointsMaterial({ map: sprite, color: 0xbff4ff, size: 0.045, sizeAttenuation: true,
    transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false });
  return { lines: new THREE.LineSegments(lineGeo, lineMat), nodes: new THREE.Points(nodeGeo, nodeMat), lineMat, nodeMat };
}
