// The 3D side: a transparent three.js canvas over the mirrored webcam, the
// hologram shader, the procedural props and .glb/.gltf loading from disk
// (and from the free library, whose bytes arrive through ATLAS: parseModel).
//
// Everything is bundled (three comes from node_modules), and models opened
// from disk are read WITHOUT a URL: the dashboard's CSP allows neither
// `connect-src blob:` nor `img-src blob:`, so a GLTFLoader that fetched
// object URLs would be refused. Files are read as ArrayBuffers, parsed with
// GLTFLoader.parse, and images are decoded with createImageBitmap(Blob),
// which never fetches anything. A model that points at a file that was not
// picked gets nothing.
import * as THREE from "three";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";

export const MODEL_SIZE = 1.6;   // every prop is normalised to this largest extent

// ----------------------------------------------------------- hologram look

const HOLO_VERT = /* glsl */`
  varying vec3 vNormalV;
  varying vec3 vViewPos;
  varying vec3 vWorldPos;
  uniform float uHasNormal;
  void main() {
    vec3 n = uHasNormal > 0.5 ? normal : normalize(position);
    vNormalV = normalize(normalMatrix * n);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vViewPos = -mv.xyz;
    vWorldPos = (modelMatrix * vec4(position, 1.0)).xyz;
    gl_Position = projectionMatrix * mv;
  }`;

const HOLO_FRAG = /* glsl */`
  uniform float uTime;
  uniform vec3 uColor;
  uniform float uOpacity;
  uniform float uRim;
  uniform float uBase;
  varying vec3 vNormalV;
  varying vec3 vViewPos;
  varying vec3 vWorldPos;
  float hash(float x) { return fract(sin(x * 91.17) * 43758.5453); }
  void main() {
    vec3 n = normalize(vNormalV);
    if (!gl_FrontFacing) n = -n;
    float fres = pow(1.0 - abs(dot(n, normalize(vViewPos))), 2.2);
    float scan = 0.8 + 0.2 * sin(gl_FragCoord.y * 1.6 - uTime * 8.0);
    float band = smoothstep(0.0, 0.08, abs(fract(vWorldPos.y * 0.6 - uTime * 0.35) - 0.5));
    float flicker = 0.9 + 0.1 * hash(floor(uTime * 24.0));
    float a = (uRim * fres + uBase) * scan * flicker * mix(1.35, 1.0, band);
    gl_FragColor = vec4(uColor * a, a * uOpacity);
  }`;

const holoTime = { value: 0 };

function holoMaterial({
  color = 0x38f3ff, opacity = 1, rim = 1.6, base = 0.12, hasNormal = true,
} = {}): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    vertexShader: HOLO_VERT, fragmentShader: HOLO_FRAG,
    uniforms: {
      uTime: holoTime, uColor: { value: new THREE.Color(color) },
      uOpacity: { value: opacity }, uRim: { value: rim }, uBase: { value: base },
      uHasNormal: { value: hasNormal ? 1 : 0 },
    },
    transparent: true, depthWrite: false, side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending,
  });
}
// Created once per page visit (makeMaterials) and disposed with the stage.
interface Looks {
  holo: THREE.ShaderMaterial; lines: THREE.ShaderMaterial;
  fill: THREE.ShaderMaterial; model: THREE.ShaderMaterial;
}
let LOOKS: Looks | null = null;
function looks(): Looks {
  LOOKS ??= {
    holo: holoMaterial(),
    lines: holoMaterial({ rim: 0.8, base: 0.45, hasNormal: false }),
    fill: holoMaterial({ rim: 1.2, base: 0.5 }),
    model: holoMaterial({ rim: 1.4, base: 0.24 }),
  };
  return LOOKS;
}

type Drawable = THREE.Mesh | THREE.Line | THREE.Points;
const isDrawable = (o: THREE.Object3D): o is Drawable =>
  (o as THREE.Mesh).isMesh === true || (o as THREE.Line).isLine === true
  || (o as THREE.Points).isPoints === true;

interface Dressed {
  holoMat?: THREE.Material;
  solidMat?: THREE.Material | THREE.Material[];
  holoOnly?: boolean;
}
const ud = (o: THREE.Object3D): Dressed => o.userData as Dressed;

const solidFor = (line: boolean): THREE.Material => (line
  ? new THREE.LineBasicMaterial({ color: 0x9fdcff })
  : new THREE.MeshStandardMaterial({
    color: 0x8fb7c9, metalness: 0.55, roughness: 0.32, emissive: 0x062430,
  }));

/** Give every mesh/line a hologram material and a solid alternative. */
function dress<T extends THREE.Object3D>(obj: T): T {
  obj.traverse((o) => {
    if (!isDrawable(o)) return;
    const line = (o as THREE.Line).isLine === true;
    const d = ud(o);
    let solid = d.solidMat ?? solidFor(line);
    if (line && !(solid as THREE.LineBasicMaterial).isLineBasicMaterial) solid = solidFor(true);
    d.solidMat = solid;
    d.holoMat ??= line ? looks().lines : looks().holo;
  });
  return obj;
}

export function applyLook(root: THREE.Object3D, hologram: boolean): void {
  root.traverse((o) => {
    const d = ud(o);
    if (isDrawable(o) && d.holoMat && d.solidMat) {
      o.material = hologram ? d.holoMat : d.solidMat;
    }
    if (d.holoOnly) o.visible = hologram;
  });
}

// --------------------------------------------------------- procedural props

function globe(): THREE.Group {
  const g = new THREE.Group();
  const pts: number[] = [];
  const R = 1;
  for (let lat = -75; lat <= 75; lat += 15) {
    const y = Math.sin(THREE.MathUtils.degToRad(lat)) * R;
    const r = Math.cos(THREE.MathUtils.degToRad(lat)) * R;
    for (let i = 0; i < 64; i++) {
      const a0 = (i / 64) * Math.PI * 2, a1 = ((i + 1) / 64) * Math.PI * 2;
      pts.push(r * Math.cos(a0), y, r * Math.sin(a0), r * Math.cos(a1), y, r * Math.sin(a1));
    }
  }
  for (let lon = 0; lon < 180; lon += 15) {
    const a = THREE.MathUtils.degToRad(lon);
    for (let i = 0; i < 64; i++) {
      const t0 = (i / 64) * Math.PI * 2, t1 = ((i + 1) / 64) * Math.PI * 2;
      pts.push(
        Math.cos(t0) * Math.cos(a) * R, Math.sin(t0) * R, Math.cos(t0) * Math.sin(a) * R,
        Math.cos(t1) * Math.cos(a) * R, Math.sin(t1) * R, Math.cos(t1) * Math.sin(a) * R);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  g.add(new THREE.LineSegments(geo));
  const core = new THREE.Mesh(new THREE.SphereGeometry(R * 0.985, 48, 32));
  ud(core).solidMat = new THREE.MeshStandardMaterial({
    color: 0x1d5a78, metalness: 0.2, roughness: 0.6, emissive: 0x041722 });
  g.add(core);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(R * 1.32, 0.012, 8, 128));
  ring.rotation.x = Math.PI / 2.3;
  g.add(ring);
  return dress(g);
}

function torusKnot(): THREE.Group {
  const g = new THREE.Group();
  g.add(new THREE.Mesh(new THREE.TorusKnotGeometry(0.62, 0.19, 220, 28)));
  const wire = new THREE.LineSegments(new THREE.WireframeGeometry(
    new THREE.TorusKnotGeometry(0.62, 0.195, 110, 10)));
  ud(wire).holoOnly = true;
  g.add(wire);
  return dress(g);
}

function crystal(): THREE.Group {
  const g = new THREE.Group();
  const geo = new THREE.IcosahedronGeometry(0.8, 0);
  geo.scale(0.75, 1.25, 0.75);
  const mesh = new THREE.Mesh(geo);
  ud(mesh).solidMat = new THREE.MeshPhysicalMaterial({
    color: 0xa8f0ff, metalness: 0, roughness: 0.05, transmission: 0.6, thickness: 0.6,
    flatShading: true, emissive: 0x0a2a33 });
  g.add(mesh);
  g.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo)));
  const inner = new THREE.Mesh(new THREE.OctahedronGeometry(0.28, 0));
  ud(inner).holoOnly = true;
  g.add(inner);
  return dress(g);
}

// Block letters from bars, for the ATLAS mark. Coordinates in a 0..1 x 0..1.4 cell.
const GLYPHS: Record<string, ReadonlyArray<readonly [number, number, number, number]>> = {
  A: [[0, 0, 0.5, 1.4], [0.5, 1.4, 1, 0], [0.22, 0.55, 0.78, 0.55]],
  T: [[0, 1.4, 1, 1.4], [0.5, 1.4, 0.5, 0]],
  L: [[0, 1.4, 0, 0], [0, 0, 0.9, 0]],
  S: [[1, 1.4, 0, 1.4], [0, 1.4, 0, 0.7], [0, 0.7, 1, 0.7], [1, 0.7, 1, 0], [1, 0, 0, 0]],
};

function atlasMark(): THREE.Group {
  const g = new THREE.Group();
  const bar = 0.2, depth = 0.22, adv = 1.4;
  const word = "ATLAS";
  const letters = new THREE.Group();
  [...word].forEach((ch, i) => {
    for (const [x0, y0, x1, y1] of GLYPHS[ch] ?? []) {
      const len = Math.hypot(x1 - x0, y1 - y0) + bar;
      const m = new THREE.Mesh(new THREE.BoxGeometry(len, bar, depth));
      m.position.set(i * adv + (x0 + x1) / 2, (y0 + y1) / 2, 0);
      m.rotation.z = Math.atan2(y1 - y0, x1 - x0);
      ud(m).holoMat = looks().fill;
      letters.add(m);
    }
  });
  letters.position.set(-((word.length - 1) * adv + 1) / 2, -0.7, 0);
  g.add(letters);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(4.0, 0.07, 10, 160));
  ring.scale.set(1, 0.42, 1);
  g.add(ring);
  const ring2 = new THREE.Mesh(new THREE.TorusGeometry(4.35, 0.025, 6, 160));
  ring2.scale.set(1, 0.47, 1);
  ud(ring2).holoOnly = true;
  g.add(ring2);
  return dress(g);
}

/** A built-in model. `line` is what ATLAS says when it is given one. */
export interface PropDef { id: string; name: string; glyph: string; make: () => THREE.Object3D; line: string }

export const PROPS: readonly PropDef[] = [
  { id: "globe", name: "Globe", glyph: "◍", make: globe, line: "Got the globe." },
  { id: "knot", name: "Torus knot", glyph: "∞", make: torusKnot, line: "A torus knot, safely in hand." },
  { id: "crystal", name: "Crystal", glyph: "◆", make: crystal, line: "Crystal secured." },
  { id: "atlas", name: "ATLAS mark", glyph: "A", make: atlasMark, line: "Caught it. That one is mine." },
];

/** A material that is the same in both looks: text and covers must read. */
function both(o: THREE.Object3D, m: THREE.Material): void {
  ud(o).holoMat = m;
  ud(o).solidMat = m;
}

function edgeFrame(w: number, h: number, d = 0): THREE.LineSegments {
  const frame = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(w, h, d)));
  return dress(frame);
}

/**
 * A floating data panel: a plane carrying a canvas the caller draws on
 * (panelCanvas.ts) and an edge frame. `redraw()` re-uploads the canvas.
 */
export function makePanel(canvas: HTMLCanvasElement): { object: THREE.Group; redraw: () => void } {
  const g = new THREE.Group();
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, side: THREE.DoubleSide, depthWrite: false });
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(2.4, 1.5), mat);
  both(plane, mat);
  g.add(plane);
  g.add(edgeFrame(2.44, 1.54));
  return { object: g, redraw: () => { tex.needsUpdate = true; } };
}

/**
 * An ATLAS Academy product as a 3D card: its cover on the front (a bundled,
 * same-origin image, or a canvas face when it has none) and an edge frame.
 */
export function makeCard(front: { url: string } | { canvas: HTMLCanvasElement }): THREE.Group {
  const g = new THREE.Group();
  const tex = "url" in front ? new THREE.TextureLoader().load(front.url) : new THREE.CanvasTexture(front.canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  const face = new THREE.MeshBasicMaterial({ map: tex });
  const side = new THREE.MeshStandardMaterial({ color: 0x0b1a22, metalness: 0.3, roughness: 0.6 });
  // Box faces: +x, -x, +y, -y, +z (front), -z (back).
  // Not dressed: the cover keeps its own look in both modes.
  const card = new THREE.Mesh(new THREE.BoxGeometry(1.0, 1.41, 0.04), [side, side, side, side, face, side]);
  g.add(card);
  g.add(edgeFrame(1.02, 1.43, 0.05));
  return g;
}

/**
 * Clone a dressed prop. Object3D.clone() deep-copies userData through JSON,
 * which would turn the stored materials into plain objects, so the material
 * references are carried across by walking both trees in step.
 */
export function cloneProp(src: THREE.Object3D): THREE.Object3D {
  const copy = src.clone(true);
  const a: THREE.Object3D[] = [], b: THREE.Object3D[] = [];
  src.traverse((o) => a.push(o));
  copy.traverse((o) => b.push(o));
  a.forEach((o, i) => {
    const from = ud(o), to = b[i];
    if (!to) return;
    if (from.holoMat) ud(to).holoMat = from.holoMat;
    if (from.solidMat) ud(to).solidMat = from.solidMat;
  });
  return copy;
}

/** Centre a model on the origin and scale its largest extent to MODEL_SIZE. */
export function normalise(obj: THREE.Object3D, size = MODEL_SIZE): THREE.Group {
  const wrap = new THREE.Group();
  wrap.add(obj);
  obj.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(obj);
  const dim = box.getSize(new THREE.Vector3());
  const max = Math.max(dim.x, dim.y, dim.z);
  const outer = new THREE.Group();
  outer.add(wrap);
  if (!Number.isFinite(max) || max <= 0) return outer;
  obj.position.sub(box.getCenter(new THREE.Vector3()));
  wrap.scale.setScalar(size / max);
  return outer;
}

// ------------------------------------------------------- loading from disk

const decodeDataUri = (uri: string): ArrayBuffer | null => {
  const m = /^data:([^;,]*)(;base64)?,(.*)$/s.exec(uri);
  if (!m) return null;
  const payload = m[3] ?? "";
  if (m[2]) {
    const bin = atob(payload);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }
  return new TextEncoder().encode(decodeURIComponent(payload)).buffer as ArrayBuffer;
};

const baseName = (uri: string): string => {
  try { return decodeURIComponent(uri.split(/[\\/]/).pop() ?? uri); } catch { return uri; }
};

interface GltfJson {
  buffers?: Array<{ uri?: string; byteLength?: number }>;
  bufferViews?: Array<{ buffer: number; byteOffset?: number; byteLength: number }>;
  images?: Array<{ uri?: string; bufferView?: number; mimeType?: string }>;
  textures?: Array<{ source?: number; sampler?: number; name?: string }>;
  samplers?: Array<{ magFilter?: number; minFilter?: number; wrapS?: number; wrapT?: number }>;
}
interface ParserLike {
  json: GltfJson;
  extensions: Record<string, { body?: ArrayBuffer } | undefined>;
  associations: Map<unknown, unknown>;
  getDependency(type: "bufferView", index: number): Promise<ArrayBuffer>;
}

const WRAP: Record<number, THREE.Wrapping> = {
  33071: THREE.ClampToEdgeWrapping, 33648: THREE.MirroredRepeatWrapping, 10497: THREE.RepeatWrapping,
};
const FILTER: Record<number, THREE.TextureFilter> = {
  9728: THREE.NearestFilter, 9729: THREE.LinearFilter,
  9984: THREE.NearestMipmapNearestFilter, 9985: THREE.LinearMipmapNearestFilter,
  9986: THREE.NearestMipmapLinearFilter, 9987: THREE.LinearMipmapLinearFilter,
};

/**
 * A GLTFLoader plugin that reads external buffers and every image from the
 * picked files (or the GLB itself), with no URL and no fetch.
 */
function localFilesPlugin(files: Map<string, ArrayBuffer>) {
  return (parser: ParserLike) => ({
    name: "AIRGLASS_local_files",

    loadBufferView(index: number): Promise<ArrayBuffer> | null {
      const view = parser.json.bufferViews?.[index];
      const buf = view ? parser.json.buffers?.[view.buffer] : undefined;
      if (!view || !buf || buf.uri === undefined) return null; // the GLB body: built-in path
      const bytes = buf.uri.startsWith("data:") ? decodeDataUri(buf.uri) : files.get(baseName(buf.uri));
      if (!bytes) return Promise.reject(new Error(`missing ${baseName(buf.uri)} (pick it too)`));
      const start = view.byteOffset ?? 0;
      return Promise.resolve(bytes.slice(start, start + view.byteLength));
    },

    async loadTexture(index: number): Promise<THREE.Texture | null> {
      const def = parser.json.textures?.[index];
      const img = def?.source !== undefined ? parser.json.images?.[def.source] : undefined;
      if (!def || !img) return null;
      let bytes: ArrayBuffer | null | undefined = null;
      if (img.bufferView !== undefined) bytes = await parser.getDependency("bufferView", img.bufferView);
      else if (img.uri?.startsWith("data:")) bytes = decodeDataUri(img.uri);
      else if (img.uri) bytes = files.get(baseName(img.uri));
      if (!bytes) return null;  // a texture we were not given: the material draws without it
      try {
        const bitmap = await createImageBitmap(new Blob([bytes], { type: img.mimeType ?? "" }),
          { imageOrientation: "none", premultiplyAlpha: "none", colorSpaceConversion: "none" });
        const tex = new THREE.Texture(bitmap as unknown as HTMLImageElement);
        tex.flipY = false;
        tex.name = def.name ?? "";
        const s = def.sampler !== undefined ? parser.json.samplers?.[def.sampler] : undefined;
        tex.magFilter = (FILTER[s?.magFilter ?? 9729] ?? THREE.LinearFilter) as THREE.MagnificationTextureFilter;
        tex.minFilter = FILTER[s?.minFilter ?? 9987] ?? THREE.LinearMipmapLinearFilter;
        tex.wrapS = WRAP[s?.wrapS ?? 10497] ?? THREE.RepeatWrapping;
        tex.wrapT = WRAP[s?.wrapT ?? 10497] ?? THREE.RepeatWrapping;
        tex.needsUpdate = true;
        parser.associations.set(tex, { textures: index });
        return tex;
      } catch {
        return null;
      }
    },
  });
}

/**
 * Load one model from files picked or dropped from disk. A .gltf that
 * references sibling .bin/.png files works when those files are picked too.
 * Nothing is fetched from the network, or from anywhere.
 */
export async function loadModelFiles(fileList: Iterable<File>): Promise<{ name: string; object: THREE.Object3D }> {
  const list = [...fileList];
  const main = list.find((f) => /\.(glb|gltf)$/i.test(f.name));
  if (!main) throw new Error("Pick a .glb or .gltf file");
  const files = new Map<string, ArrayBuffer>();
  await Promise.all(list.map(async (f) => { files.set(f.name, await f.arrayBuffer()); }));
  return parseModel(files, main.name);
}

/**
 * Parse a model from bytes already in hand, keyed by file (base) name: the
 * disk loader above, and the free library (library.ts), whose bytes came
 * through ATLAS. Same path either way: no URL, no fetch, the hologram look.
 */
export async function parseModel(files: Map<string, ArrayBuffer>, mainName: string,
): Promise<{ name: string; object: THREE.Object3D }> {
  const data = files.get(mainName);
  if (!data) throw new Error("could not read the file");
  const loader = new GLTFLoader();
  loader.register(localFilesPlugin(files) as unknown as Parameters<GLTFLoader["register"]>[0]);
  const gltf = await new Promise<GLTF>((resolve, reject) => {
    loader.parse(data, "", resolve, (e) => reject(e instanceof Error ? e : new Error(String(e))));
  });
  const root = gltf.scene;
  root.traverse((o) => {
    if (!isDrawable(o)) return;
    ud(o).solidMat = o.material;
    if ((o as THREE.Mesh).isMesh) {
      ud(o).holoMat = looks().model;
      // The hologram's rim light needs normals; some exports leave them out.
      if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
    }
  });
  return { name: mainName.replace(/\.(glb|gltf)$/i, ""), object: dress(root) };
}

// ------------------------------------------------------------------- stage

export class Stage {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
  readonly holder = new THREE.Group();   // what the hands move
  readonly base: THREE.Group;            // projector ring under the prop
  current: THREE.Group | null = null;
  hologram = true;
  unitsPerPx = 0.01;
  view = { w: 1, h: 1 };
  /** Radians/s about world y and x that decay into the slow idle turn. */
  spin = { x: 0, y: 0 };
  /** The slow idle turn when nothing holds the model. */
  autoSpin = true;
  /** How fast that idle turn is, rad/s ("spin it slowly" / "fast"). */
  idleSpin = 0.35;
  /** The projector ring under a model (not under a flat panel). */
  showBase = true;
  private readonly timer = new THREE.Timer();

  constructor(canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.camera.position.set(0, 0, 6);
    this.scene.add(new THREE.HemisphereLight(0xcff6ff, 0x0b1a22, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(3, 4, 5);
    this.scene.add(key);
    this.scene.add(this.holder);
    this.base = this.makeBase();
    this.scene.add(this.base);
  }

  private makeBase(): THREE.Group {
    const g = new THREE.Group();
    for (const [r, w] of [[0.7, 0.006], [0.8, 0.003]] as const) {
      const m = new THREE.Mesh(new THREE.TorusGeometry(r, w, 6, 128));
      m.rotation.x = Math.PI / 2;
      g.add(m);
    }
    dress(g);
    g.traverse((o) => { ud(o).holoOnly = true; });
    applyLook(g, true);
    return g;
  }

  /** Size the drawing to the stage element (not the window). */
  resize(w: number, h: number): void {
    const width = Math.max(1, Math.round(w)), height = Math.max(1, Math.round(h));
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    const vh = 2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * this.camera.position.z;
    this.unitsPerPx = vh / height;
    this.view = { w: width, h: height };
  }

  /** Stage pixels (y down) to a point on the z = 0 plane. */
  screenToWorld(x: number, y: number): THREE.Vector3 {
    return new THREE.Vector3(
      (x - this.view.w / 2) * this.unitsPerPx, -(y - this.view.h / 2) * this.unitsPerPx, 0);
  }

  worldToScreen(v: THREE.Vector3): { x: number; y: number } {
    const p = v.clone().project(this.camera);
    return { x: (p.x + 1) / 2 * this.view.w, y: (1 - p.y) / 2 * this.view.h };
  }

  /** Show a prop (an Object3D already dressed). */
  show(obj: THREE.Object3D, at: THREE.Vector3 | null = null): void {
    if (this.current) this.holder.remove(this.current);
    this.current = normalise(obj);
    this.holder.add(this.current);
    applyLook(this.current, this.hologram);
    this.holder.position.copy(at ?? new THREE.Vector3(0, 0.15, 0));
    this.holder.quaternion.identity();
    this.holder.scale.setScalar(1);
    this.spin = { x: 0, y: 0 };
  }

  clear(): void {
    if (this.current) this.holder.remove(this.current);
    this.current = null;
  }

  setHologram(on: boolean): void {
    this.hologram = on;
    if (this.current) applyLook(this.current, on);
    applyLook(this.base, on);
  }

  /** Turn the held model about world axes (slide-to-turn). */
  roll(ax: number, ay: number): void {
    const q = new THREE.Quaternion()
      .setFromAxisAngle(new THREE.Vector3(0, 1, 0), ay)
      .multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), ax));
    this.holder.quaternion.premultiply(q);
  }

  render(idle: boolean): number {
    this.timer.update();
    const dt = Math.min(this.timer.getDelta(), 0.1);
    holoTime.value = this.timer.getElapsed();
    if (this.current && idle) {
      // A released spin carries on and settles into the slow idle turn, or
      // to a stop when auto-spin is off ("stop", and the data panels).
      const k = Math.exp(-1.4 * dt);
      const rest = this.autoSpin ? this.idleSpin : 0;
      this.spin.x *= k;
      this.spin.y = rest + (this.spin.y - rest) * k;
      this.roll(this.spin.x * dt, this.spin.y * dt);
    }
    this.base.visible = this.hologram && this.showBase && Boolean(this.current);
    const s = this.holder.scale.x;
    this.base.position.set(this.holder.position.x, this.holder.position.y - 1.0 * s,
      this.holder.position.z);
    this.base.scale.setScalar(s);
    this.renderer.render(this.scene, this.camera);
    return dt;
  }

  /** Free the GPU: without this, every visit to the page leaks a context. */
  dispose(): void {
    const seen = new Set<THREE.Material | THREE.BufferGeometry | THREE.Texture>();
    this.scene.traverse((o) => {
      const d = ud(o);
      const mats = [d.holoMat, ...(Array.isArray(d.solidMat) ? d.solidMat : [d.solidMat])];
      if (isDrawable(o)) {
        seen.add(o.geometry);
        mats.push(...(Array.isArray(o.material) ? o.material : [o.material]));
      }
      for (const m of mats) {
        if (!m) continue;
        seen.add(m);
        for (const v of Object.values(m)) if (v instanceof THREE.Texture) seen.add(v);
      }
    });
    seen.forEach((x) => x.dispose());
    if (LOOKS) Object.values(LOOKS).forEach((m) => m.dispose());
    LOOKS = null;
    this.renderer.dispose();
    this.renderer.forceContextLoss();
  }
}

export { THREE };
