// The 3D side: a transparent three.js canvas over the mirrored webcam, the
// hologram shader, the procedural props and .glb/.gltf loading from disk.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

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
    // screen-space scanlines (no moire as the prop scales), plus a slow band
    float scan = 0.8 + 0.2 * sin(gl_FragCoord.y * 1.6 - uTime * 8.0);
    float band = smoothstep(0.0, 0.08, abs(fract(vWorldPos.y * 0.6 - uTime * 0.35) - 0.5));
    float flicker = 0.9 + 0.1 * hash(floor(uTime * 24.0));
    float a = (uRim * fres + uBase) * scan * flicker * mix(1.35, 1.0, band);
    gl_FragColor = vec4(uColor * a, a * uOpacity);
  }`;

const holoUniforms = { uTime: { value: 0 } };

function holoMaterial({
  color = 0x38f3ff, opacity = 1, rim = 1.6, base = 0.12, hasNormal = true,
} = {}) {
  return new THREE.ShaderMaterial({
    vertexShader: HOLO_VERT, fragmentShader: HOLO_FRAG,
    uniforms: {
      uTime: holoUniforms.uTime, uColor: { value: new THREE.Color(color) },
      uOpacity: { value: opacity }, uRim: { value: rim }, uBase: { value: base },
      uHasNormal: { value: hasNormal ? 1 : 0 },
    },
    transparent: true, depthWrite: false, side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending,
  });
}
const HOLO = holoMaterial();
const HOLO_LINES = holoMaterial({ rim: 0.8, base: 0.45, hasNormal: false });
const HOLO_FILL = holoMaterial({ rim: 1.2, base: 0.5 });   // flat faces that must read
const HOLO_MODEL = holoMaterial({ rim: 1.4, base: 0.24 }); // loaded models: some faces are flat

// A "solid" material for the procedural props when the hologram is off.
const solidFor = (kind) => new THREE.MeshStandardMaterial({
  color: kind === 'line' ? 0x9fdcff : 0x8fb7c9, metalness: 0.55, roughness: 0.32,
  emissive: 0x062430, flatShading: false,
});

/**
 * Give every mesh/line a hologram material and a solid alternative. A loaded
 * model keeps its own materials as the solid look (set before dress() runs);
 * a procedural prop gets a lit metal one unless it set its own.
 */
function dress(obj) {
  obj.traverse((o) => {
    if (!(o.isMesh || o.isLine || o.isPoints)) return;
    const line = Boolean(o.isLine);
    let solid = o.userData.solidMat ?? solidFor(line ? 'line' : 'mesh');
    if (line && !solid.isLineBasicMaterial) solid = new THREE.LineBasicMaterial({ color: 0x9fdcff });
    o.userData.solidMat = solid;
    o.userData.holoMat ??= line ? HOLO_LINES : HOLO;
  });
  return obj;
}

export function applyLook(root, hologram) {
  root.traverse((o) => {
    if (o.userData.holoMat) o.material = hologram ? o.userData.holoMat : o.userData.solidMat;
    if (o.userData.holoOnly) o.visible = hologram;
  });
}

// --------------------------------------------------------- procedural props

function globe() {
  const g = new THREE.Group();
  const pts = [];
  const R = 1;
  for (let lat = -75; lat <= 75; lat += 15) {           // parallels
    const y = Math.sin(THREE.MathUtils.degToRad(lat)) * R;
    const r = Math.cos(THREE.MathUtils.degToRad(lat)) * R;
    for (let i = 0; i < 64; i++) {
      const a0 = (i / 64) * Math.PI * 2, a1 = ((i + 1) / 64) * Math.PI * 2;
      pts.push(r * Math.cos(a0), y, r * Math.sin(a0), r * Math.cos(a1), y, r * Math.sin(a1));
    }
  }
  for (let lon = 0; lon < 180; lon += 15) {              // meridians
    const a = THREE.MathUtils.degToRad(lon);
    for (let i = 0; i < 64; i++) {
      const t0 = (i / 64) * Math.PI * 2, t1 = ((i + 1) / 64) * Math.PI * 2;
      pts.push(
        Math.cos(t0) * Math.cos(a) * R, Math.sin(t0) * R, Math.cos(t0) * Math.sin(a) * R,
        Math.cos(t1) * Math.cos(a) * R, Math.sin(t1) * R, Math.cos(t1) * Math.sin(a) * R);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
  g.add(new THREE.LineSegments(geo));
  const core = new THREE.Mesh(new THREE.SphereGeometry(R * 0.985, 48, 32));
  core.userData.solidMat = new THREE.MeshStandardMaterial({
    color: 0x1d5a78, metalness: 0.2, roughness: 0.6, emissive: 0x041722 });
  g.add(core);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(R * 1.32, 0.012, 8, 128));
  ring.rotation.x = Math.PI / 2.3;
  g.add(ring);
  return dress(g);
}

function torusKnot() {
  const g = new THREE.Group();
  const geo = new THREE.TorusKnotGeometry(0.62, 0.19, 220, 28);
  g.add(new THREE.Mesh(geo));
  const wire = new THREE.LineSegments(new THREE.WireframeGeometry(
    new THREE.TorusKnotGeometry(0.62, 0.195, 110, 10)));
  wire.userData.holoOnly = true;
  g.add(wire);
  return dress(g);
}

function crystal() {
  const g = new THREE.Group();
  const geo = new THREE.IcosahedronGeometry(0.8, 0);
  geo.scale(0.75, 1.25, 0.75);
  const mesh = new THREE.Mesh(geo);
  mesh.userData.solidMat = new THREE.MeshPhysicalMaterial({
    color: 0xa8f0ff, metalness: 0, roughness: 0.05, transmission: 0.6, thickness: 0.6,
    flatShading: true, emissive: 0x0a2a33 });
  g.add(mesh);
  g.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo)));
  const inner = new THREE.Mesh(new THREE.OctahedronGeometry(0.28, 0));
  inner.userData.holoOnly = true;
  g.add(inner);
  return dress(g);
}

// Block letters from bars, for the ATLAS mark. Coordinates in a 0..1 x 0..1.4 cell.
const GLYPHS = {
  A: [[0, 0, 0.5, 1.4], [0.5, 1.4, 1, 0], [0.22, 0.55, 0.78, 0.55]],
  T: [[0, 1.4, 1, 1.4], [0.5, 1.4, 0.5, 0]],
  L: [[0, 1.4, 0, 0], [0, 0, 0.9, 0]],
  S: [[1, 1.4, 0, 1.4], [0, 1.4, 0, 0.7], [0, 0.7, 1, 0.7], [1, 0.7, 1, 0], [1, 0, 0, 0]],
};

function atlasMark() {
  const g = new THREE.Group();
  const bar = 0.2, depth = 0.22, adv = 1.4;
  const word = 'ATLAS';
  const letters = new THREE.Group();
  [...word].forEach((ch, i) => {
    for (const [x0, y0, x1, y1] of GLYPHS[ch]) {
      const len = Math.hypot(x1 - x0, y1 - y0) + bar;
      const m = new THREE.Mesh(new THREE.BoxGeometry(len, bar, depth));
      m.position.set(i * adv + (x0 + x1) / 2, (y0 + y1) / 2, 0);
      m.rotation.z = Math.atan2(y1 - y0, x1 - x0);
      m.userData.holoMat = HOLO_FILL;
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
  ring2.userData.holoOnly = true;
  g.add(ring2);
  return dress(g);
}

export const PROPS = [
  { id: 'globe', name: 'Wireframe globe', make: globe, line: 'Got the globe.' },
  { id: 'knot', name: 'Torus knot', make: torusKnot, line: 'A torus knot, safely in hand.' },
  { id: 'crystal', name: 'Crystal', make: crystal, line: 'Crystal secured.' },
  { id: 'atlas', name: 'ATLAS mark', make: atlasMark, line: 'Caught it. That one is mine.' },
];

/**
 * Clone a dressed prop. Object3D.clone() deep-copies userData through JSON,
 * which would turn the stored materials into plain objects, so the material
 * references are carried across by walking both trees in step.
 */
export function cloneProp(src) {
  const copy = src.clone(true);
  const a = [], b = [];
  src.traverse((o) => a.push(o));
  copy.traverse((o) => b.push(o));
  a.forEach((o, i) => {
    for (const k of ['holoMat', 'solidMat']) if (o.userData[k]) b[i].userData[k] = o.userData[k];
  });
  return copy;
}

/** Centre a model on the origin and scale its largest extent to MODEL_SIZE. */
export function normalise(obj, size = MODEL_SIZE) {
  const wrap = new THREE.Group();
  wrap.add(obj);
  obj.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(obj);
  const dim = box.getSize(new THREE.Vector3());
  const max = Math.max(dim.x, dim.y, dim.z);
  if (!Number.isFinite(max) || max <= 0) return wrap;
  const c = box.getCenter(new THREE.Vector3());
  obj.position.sub(c);
  wrap.scale.setScalar(size / max);
  const outer = new THREE.Group();
  outer.add(wrap);
  return outer;
}

/**
 * Load one model from files picked or dropped from disk. A .gltf that
 * references sibling .bin/.png files works when those files are picked too.
 * Nothing is fetched from the network: every URL is a local blob: URL.
 */
export async function loadModelFiles(files) {
  const list = [...files];
  const main = list.find((f) => /\.(glb|gltf)$/i.test(f.name));
  if (!main) throw new Error('Pick a .glb or .gltf file');
  const urls = new Map(list.map((f) => [f.name, URL.createObjectURL(f)]));
  const manager = new THREE.LoadingManager();
  manager.setURLModifier((url) => {
    if (url.startsWith('blob:') || url.startsWith('data:')) return url;
    const name = decodeURIComponent(url.split(/[\\/]/).pop());
    if (urls.has(name)) return urls.get(name);
    // Refuse anything that is not one of the picked files: no network loads.
    return 'data:,';
  });
  try {
    const gltf = await new GLTFLoader(manager).loadAsync(urls.get(main.name));
    const root = gltf.scene;
    root.traverse((o) => {
      if (!(o.isMesh || o.isLine || o.isPoints)) return;
      o.userData.solidMat = o.material;
      if (o.isMesh) o.userData.holoMat = HOLO_MODEL;
      // The hologram's rim light needs normals; some exports leave them out.
      if (o.isMesh && !o.geometry.attributes.normal) o.geometry.computeVertexNormals();
    });
    return { name: main.name.replace(/\.(glb|gltf)$/i, ''), object: dress(root) };
  } finally {
    setTimeout(() => urls.forEach((u) => URL.revokeObjectURL(u)), 5000);
  }
}

// ------------------------------------------------------------------- stage

export class Stage {
  constructor(canvas) {
    this.renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
    this.camera.position.set(0, 0, 6);
    this.scene.add(new THREE.HemisphereLight(0xcff6ff, 0x0b1a22, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(3, 4, 5);
    this.scene.add(key);

    this.holder = new THREE.Group();        // what the hands move
    this.scene.add(this.holder);
    this.base = this.makeBase();             // projector ring under the prop
    this.scene.add(this.base);
    this.current = null;
    this.hologram = true;
    this.timer = new THREE.Timer();
    this.resize();
  }

  makeBase() {
    const g = new THREE.Group();
    for (const [r, w] of [[0.7, 0.006], [0.8, 0.003]]) {
      const m = new THREE.Mesh(new THREE.TorusGeometry(r, w, 6, 128));
      m.rotation.x = Math.PI / 2;
      g.add(m);
    }
    dress(g);
    g.traverse((o) => { o.userData.holoOnly = true; });
    applyLook(g, true);
    return g;
  }

  resize() {
    const w = window.innerWidth, h = window.innerHeight;
    // The window can move to a display with another pixel ratio.
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    // World units per pixel on the z = 0 plane.
    const vh = 2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * this.camera.position.z;
    this.unitsPerPx = vh / h;
    this.view = { w, h };
  }

  /** Viewport pixels (y down) to a point on the z = 0 plane. */
  screenToWorld(x, y) {
    return new THREE.Vector3(
      (x - this.view.w / 2) * this.unitsPerPx, -(y - this.view.h / 2) * this.unitsPerPx, 0);
  }

  worldToScreen(v) {
    const p = v.clone().project(this.camera);
    return { x: (p.x + 1) / 2 * this.view.w, y: (1 - p.y) / 2 * this.view.h };
  }

  /** Show a prop (an Object3D already dressed); returns it. */
  show(obj, at = null) {
    if (this.current) this.holder.remove(this.current);
    this.current = normalise(obj);
    this.holder.add(this.current);
    applyLook(this.current, this.hologram);
    this.holder.position.copy(at ?? new THREE.Vector3(0, 0, 0));
    this.holder.quaternion.identity();
    this.holder.scale.setScalar(1);
    this.base.visible = this.hologram;
    return this.current;
  }

  clear() {
    if (this.current) this.holder.remove(this.current);
    this.current = null;
  }

  setHologram(on) {
    this.hologram = on;
    if (this.current) applyLook(this.current, on);
    applyLook(this.base, on);
  }

  render(idleSpin) {
    this.timer.update();
    const dt = Math.min(this.timer.getDelta(), 0.1);
    holoUniforms.uTime.value = this.timer.getElapsed();
    if (this.current && idleSpin) this.current.rotation.y += dt * 0.35;
    this.base.visible = this.hologram && Boolean(this.current);
    const k = this.holder.scale.x;
    this.base.position.set(this.holder.position.x, this.holder.position.y - 1.0 * k,
      this.holder.position.z);
    this.base.scale.setScalar(k);
    this.renderer.render(this.scene, this.camera);
    return dt;
  }
}

export { THREE };
