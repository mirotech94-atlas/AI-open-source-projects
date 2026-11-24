// The ATLAS Academy 3D kit (2026-10-10, Miro: "make the design for each
// thumbnail etc animated and 3D and actually interesting").
//
// Art direction: the workbench at night. The 2D slides are a workbench; here
// the bench is a dark, faintly reflective ink surface printed with the n8n
// editor's own dot grid, receding in perspective. On it stand machined
// objects -- rounded node tiles with light glyphs, cables, a hub, a box --
// lit by one cool white key and a light-blue rim. Royal blue is the only
// thing that glows (the bloom threshold only lets the blue through), so the eye goes
// where the lesson is "now", as in the slides. Subjects sit in the right
// third: the slides' text lives on the left. No logos, no faces.
//
// Every scene is a pure function of time: kit.render(t) draws t seconds in,
// and every motion is periodic in L, so a loop's last frame meets its first.
// The intro sting is the "mark" scene at t in [-STING, 0): it assembles the
// ATLAS mark and glides it to where the mark loop starts.
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { Reflector } from 'three/addons/objects/Reflector.js';

const Q = new URLSearchParams(location.search);
const NAME = Q.get('scene') || 'n8n';
const W = +(Q.get('w') || 1920), H = +(Q.get('h') || 1080);
const STILL = Q.get('mode') === 'still';
const L = 10;            // loop seconds
const STING = 4;         // intro sting seconds
const TAU = Math.PI * 2;
// The navy video system (Miro, 2026-10-17), the exact tokens of
// video_tokens.py: ink is the navy field, paper the type, blue the one thing
// that glows, blueL (blue-text) its second voice, body navy-raised #182540.
// The committed loops and stills predate this until the kit is rebuilt,
// except the 2026-10-18 series' (explained, vault, future: kit-ai.json).
const C = { ink: 0x101b30, paper: 0xf6f8ff, blue: 0x526de6, blueL: 0x9db0f5,
            muted: 0xb4bdd6, body: 0x182540 };

const fract = (x) => x - Math.floor(x);
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const ease = (x) => { x = clamp(x); return x * x * (3 - 2 * x); };
const easeOut = (x) => 1 - Math.pow(1 - clamp(x), 3);
const wave = (t, k = 1, ph = 0) => Math.sin(TAU * (k * t / L + ph));
// A soft pulse once per loop, centred at phase ph: 0..1.
const blip = (t, ph, width = 0.08) => {
  let d = Math.abs(fract(t / L - ph + 0.5) - 0.5);
  return Math.max(0, 1 - d / width) ** 2;
};

// ------------------------------------------------------------- renderer
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(1);
renderer.setSize(W, H);
renderer.toneMapping = THREE.NeutralToneMapping;
renderer.toneMappingExposure = 1.0;
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(C.ink);
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.5;

const camera = new THREE.PerspectiveCamera(STILL ? 32 : 35, W / H, 0.1, 200);

const target = new THREE.WebGLRenderTarget(W, H, { type: THREE.HalfFloatType, samples: 4 });
const composer = new EffectComposer(renderer, target);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(W, H), 0.7, 0.5, 1.35);
composer.addPass(bloom);
composer.addPass(new OutputPass());

// ---------------------------------------------------------------- light
scene.add(new THREE.HemisphereLight(C.blueL, C.ink, 0.35));
const key = new THREE.DirectionalLight(0xf6f8ff, 1.8);
key.position.set(6, 7, 6);
scene.add(key);
const rim = new THREE.DirectionalLight(C.blueL, 2.0);
rim.position.set(-6, 4, -7);
scene.add(rim);

// ---------------------------------------------------------------- bench
function bench(cx = 3) {
  const mirror = new Reflector(new THREE.PlaneGeometry(90, 90), {
    textureWidth: Math.round(W / 2), textureHeight: Math.round(H / 2), color: 0x182540 });
  mirror.rotation.x = -Math.PI / 2;
  scene.add(mirror);
  const dots = new THREE.Mesh(new THREE.PlaneGeometry(90, 90), new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { uInk: { value: new THREE.Color(C.ink) }, uPaper: { value: new THREE.Color(C.paper) },
                uC: { value: new THREE.Vector2(cx, 0) } },
    vertexShader: `varying vec3 vW; void main(){ vec4 w = modelMatrix*vec4(position,1.);
      vW = w.xyz; gl_Position = projectionMatrix*viewMatrix*w; }`,
    fragmentShader: `uniform vec3 uInk; uniform vec3 uPaper; uniform vec2 uC; varying vec3 vW;
      void main(){ vec2 g = fract(vW.xz/0.42)-0.5; float d = length(g);
        float aa = fwidth(d)*1.2;
        float dt = 1.0-smoothstep(0.045-aa,0.045+aa,d);
        float dist = length(vW.xz-uC);
        float near = 1.0-smoothstep(3.0,15.0,dist);
        vec3 col = mix(uInk, uPaper, dt*0.30*near);
        float a = mix(0.93, 0.72, near) + dt*0.2*near;
        gl_FragColor = vec4(col, clamp(a,0.,1.)); }`,
  }));
  dots.rotation.x = -Math.PI / 2;
  dots.position.y = 0.002;
  scene.add(dots);
  // A deep blue glow behind the subject (screen space), fading to navy
  // before the horizon so the bench has no hard edge.
  scene.background = canvasTex(960, 540, (g, w, h) => {
    g.fillStyle = '#101b30'; g.fillRect(0, 0, w, h);
    const r = g.createRadialGradient(w * 0.72, h * 0.12, 0, w * 0.72, h * 0.12, w * 0.55);
    r.addColorStop(0, 'rgba(82,109,230,0.30)');
    r.addColorStop(0.4, 'rgba(24,37,64,0.55)');
    r.addColorStop(1, 'rgba(16,27,48,0)');
    g.fillStyle = r; g.fillRect(0, 0, w, h);
  });
}

// -------------------------------------------------------------- helpers
function canvasTex(w, h, draw) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const g = c.getContext('2d');
  draw(g, w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

// The slides' glyphs (tutorial_design.py), drawn in a 100-unit box.
const GLYPH = {
  bolt: [{ d: 'M8 -38 L-22 6 H4 L-8 38 L24 -8 H-2 Z' }],
  globe: [{ circle: [0, 0, 32] }, { d: 'M-32 0 H32 M0 -32 C-18 -14 -18 14 0 32 C18 14 18 -14 0 -32' }],
  chat: [{ d: 'M-30 -24 H30 V14 H-6 L-22 30 V14 H-30 Z' }],
  form: [{ d: 'M-26 -34 H26 V34 H-26 Z M-14 -14 H14 M-14 2 H14 M-14 18 H4' }],
  db: [{ ellipse: [0, -20, 28, 10] }, { d: 'M-28 -20 V22 C-28 34 28 34 28 22 V-20 M-28 2 C-28 14 28 14 28 2' }],
  file: [{ d: 'M-22 -32 H8 L24 -16 V32 H-22 Z M8 -32 V-16 H24' }],
  cal: [{ d: 'M-28 -24 H28 V30 H-28 Z M-28 -8 H28 M-14 -34 V-18 M14 -34 V-18' }],
  loop: [{ d: 'M-24 -10 A26 26 0 1 1 -8 22 M-24 -10 L-30 -30 M-24 -10 L-4 -16' }],
  play: [{ d: 'M-14 -24 L24 0 L-14 24 Z', fill: true }],
  heart: [{ d: 'M0 30 C-40 4 -34 -30 -12 -28 C-4 -27 0 -20 0 -16 C0 -20 4 -27 12 -28 C34 -30 40 4 0 30 Z' }],
  bell: [{ d: 'M0 -32 C14 -32 22 -22 22 -8 V8 L30 20 H-30 L-22 8 V-8 C-22 -22 -14 -32 0 -32 Z', fill: true },
         { circle: [0, 28, 6], fill: true }],
  spark: [{ d: 'M0 -34 C4 -8 8 -4 34 0 C8 4 4 8 0 34 C-4 8 -8 4 -34 0 C-8 -4 -4 -8 0 -34 Z', fill: true }],
  // 2026-10-11, the topic loops
  wrench: [{ d: 'M-26 26 L6 -6 M6 -6 C0 -20 10 -34 26 -30 L14 -18 L18 -10 L26 -14 L30 -26 C34 -10 20 0 6 -6' }],
  gear: [{ circle: [0, 0, 12] }, { d: 'M0 -34 V-22 M0 22 V34 M-34 0 H-22 M22 0 H34 M-24 -24 L-16 -16 M16 16 L24 24 M-24 24 L-16 16 M16 -16 L24 -24' }, { circle: [0, 0, 22] }],
  term: [{ d: 'M-32 -26 H32 V26 H-32 Z M-20 -8 L-8 2 L-20 12 M0 12 H18' }],
  lock: [{ d: 'M-22 -4 H22 V30 H-22 Z M-14 -4 V-16 C-14 -34 14 -34 14 -16 V-4 M0 8 V18' }],
  brace: [{ d: 'M-12 -32 C-24 -32 -20 -12 -20 -6 C-20 0 -28 0 -28 0 C-28 0 -20 0 -20 6 C-20 12 -24 32 -12 32 M12 -32 C24 -32 20 -12 20 -6 C20 0 28 0 28 0 C28 0 20 0 20 6 C20 12 24 32 12 32' }],
  cloud: [{ d: 'M-26 16 C-40 16 -40 -4 -26 -4 C-26 -20 -4 -26 4 -12 C14 -24 32 -14 28 0 C40 2 38 16 26 16 Z' }],
};

function drawGlyph(g, parts, size, lw = 9, colour = "#fff") {
  g.save();
  g.translate(size / 2, size / 2);
  g.scale(size / 100, size / 100);
  g.lineWidth = lw; g.lineCap = 'round'; g.lineJoin = 'round';
  g.strokeStyle = colour; g.fillStyle = colour;
  for (const p of parts) {
    let path;
    if (p.d) path = new Path2D(p.d);
    else {
      path = new Path2D();
      if (p.circle) path.arc(p.circle[0], p.circle[1], p.circle[2], 0, TAU);
      if (p.ellipse) path.ellipse(p.ellipse[0], p.ellipse[1], p.ellipse[2], p.ellipse[3], 0, 0, TAU);
    }
    if (p.fill) g.fill(path); else g.stroke(path);
  }
  g.restore();
}

const texCache = new Map();
function glyphTex(name) {
  if (!texCache.has(name)) texCache.set(name, canvasTex(256, 256, (g, w) => drawGlyph(g, GLYPH[name], w)));
  return texCache.get(name);
}

function textTex(lines, { w = 1024, h = 256, font = '600 150px IBM Plex Sans', color = '#f6f8ff',
                          align = 'center', bg = null } = {}) {
  return canvasTex(w, h, (g) => {
    if (bg) { g.fillStyle = bg; g.fillRect(0, 0, w, h); }
    g.fillStyle = color; g.font = font; g.textAlign = align; g.textBaseline = 'middle';
    const n = lines.length;
    lines.forEach((ln, i) => g.fillText(ln, align === 'center' ? w / 2 : 40,
                                        h * (i + 1) / (n + 1)));
  });
}

const M = {
  body: () => new THREE.MeshPhysicalMaterial({ color: C.body, roughness: 0.42, metalness: 0.05,
                                               clearcoat: 1, clearcoatRoughness: 0.18 }),
  paper: () => new THREE.MeshPhysicalMaterial({ color: C.paper, roughness: 0.55, clearcoat: 0.4,
                                                clearcoatRoughness: 0.4 }),
  blue: (e = 0.35) => new THREE.MeshPhysicalMaterial({ color: C.blue, emissive: C.blue,
                       emissiveIntensity: e, roughness: 0.3, metalness: 0.1, clearcoat: 1 }),
  metal: () => new THREE.MeshPhysicalMaterial({ color: 0x182540, roughness: 0.32, metalness: 0.85 }),
  // a card's body: raised and dark, so cards carry light type like the slides
  card: () => new THREE.MeshPhysicalMaterial({ color: C.body, roughness: 0.5, clearcoat: 0.5,
                                               clearcoatRoughness: 0.35 }),
  glow: (k = 4) => new THREE.MeshBasicMaterial({ color: new THREE.Color(C.blue).multiplyScalar(k) }),
  wire: () => new THREE.MeshStandardMaterial({ color: C.blueL, roughness: 0.4, metalness: 0.2,
                                               emissive: C.blueL, emissiveIntensity: 0.12 }),
};

// A face for a tile: a glyph that is lit (blue, glowing) or quiet (light blue).
function face(name, size, colour) {
  const mat = new THREE.MeshStandardMaterial({ map: glyphTex(name), transparent: true,
    color: colour, emissive: colour, emissiveMap: glyphTex(name), emissiveIntensity: 0.4,
    roughness: 0.5, depthWrite: false });
  return new THREE.Mesh(new THREE.PlaneGeometry(size, size), mat);
}

// An n8n-style node tile: a rounded block with a glyph on its face.
function tile(glyph, { size = 1.1, depth = 0.34, lit = false } = {}) {
  const g = new THREE.Group();
  const body = new THREE.Mesh(new RoundedBoxGeometry(size, size, depth, 5, 0.2), M.body());
  g.add(body);
  // A lit tile carries a blue band round its waist.
  const rimMat = lit ? M.blue(0.9) : null;
  if (lit) {
    const rimMesh = new THREE.Mesh(new RoundedBoxGeometry(size * 1.03, size * 1.03, depth * 0.3, 5, 0.2), rimMat);
    g.add(rimMesh);
  }
  const f = face(glyph, size * 0.6, lit ? C.blue : C.blueL);
  f.position.z = depth / 2 + 0.004;
  g.add(f);
  g.userData = { face: f, rim: rimMat, lit };
  return g;
}

function cable(curve, { r = 0.035, mat = M.wire() } = {}) {
  return new THREE.Mesh(new THREE.TubeGeometry(curve, 96, r, 10, false), mat);
}

function bezier(a, b, bend = 0.9) {
  const A = new THREE.Vector3(...a), B = new THREE.Vector3(...b);
  const dx = Math.max(0.4, Math.abs(B.x - A.x) * bend);
  return new THREE.CubicBezierCurve3(A, A.clone().add(new THREE.Vector3(dx, 0, 0)),
                                     B.clone().add(new THREE.Vector3(-dx, 0, 0)), B);
}

// Blue beads travelling a curve: `k` laps per loop (integer, so it loops).
function pulses(parent, curve, { n = 2, k = 1, phase = 0, r = 0.075 } = {}) {
  const beads = [];
  for (let i = 0; i < n; i++) {
    const m = new THREE.Mesh(new THREE.SphereGeometry(r, 20, 14), M.glow(5));
    parent.add(m);
    beads.push(m);
  }
  return (t) => beads.forEach((m, i) => {
    const u = fract(k * t / L + phase + i / n);
    m.position.copy(curve.getPointAt(u));
    const s = Math.sin(Math.PI * u);           // swell in the middle, vanish at the ends
    m.scale.setScalar(0.25 + 0.75 * Math.min(1, s * 3));
  });
}

function cardMesh(w, h, d, frontTex, { mat = M.card(), r = 0.08 } = {}) {
  const g = new THREE.Group();
  g.add(new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 4, r), mat));
  if (frontTex) {
    const f = new THREE.Mesh(new THREE.PlaneGeometry(w * 0.94, h * 0.94),
      new THREE.MeshStandardMaterial({ map: frontTex, transparent: true, roughness: 0.6, depthWrite: false }));
    f.position.z = d / 2 + 0.003;
    g.add(f);
  }
  return g;
}

// ------------------------------------------------------------ the scenes
// Each returns { cam:[x,y,z], at:[x,y,z], update(t) }.
const SCENES = {};

// n8n: a chain of node tiles, data pulses running along the wires.
SCENES.n8n = (root) => {
  const specs = [['bolt', [2.2, 1.25, 0.6]], ['globe', [3.4, 1.75, -0.3]],
                 ['spark', [4.6, 1.2, 0.2]], ['form', [5.8, 1.95, -0.8]], ['db', [5.8, 0.6, 0.5]]];
  const tiles = specs.map(([g, p], i) => {
    const t = tile(g, { lit: i === 2 });
    t.position.set(...p);
    t.rotation.y = -0.22;
    root.add(t);
    return t;
  });
  const links = [[0, 1], [1, 2], [2, 3], [2, 4]];
  const runs = links.map(([a, b], i) => {
    const A = tiles[a].position, B = tiles[b].position;
    const curve = bezier([A.x + 0.58, A.y, A.z], [B.x - 0.58, B.y, B.z]);
    root.add(cable(curve));
    return { curve, go: pulses(root, curve, { n: 2, k: 1, phase: i * 0.13 }) };
  });
  return {
    cam: [0.9, 2.6, 8.8], at: [2.1, 1.3, 0],
    update(t) {
      tiles.forEach((x, i) => {
        x.position.y = specs[i][1][1] + 0.07 * wave(t, 1, i * 0.21);
        x.rotation.y = -0.22 + 0.05 * wave(t, 1, i * 0.17 + 0.3);
        // a node flashes as a bead arrives
        const hit = Math.max(blip(t, (i * 0.13 + 0.97) % 1, 0.06), blip(t, (i * 0.13 + 0.47) % 1, 0.06));
        x.userData.face.material.emissiveIntensity = (x.userData.lit ? 1.6 : 0.4) + 2.2 * hit;
      });
      runs.forEach((r) => r.go(t));
    },
  };
};

// The hub with its tools: Claude in the middle, MCP modules orbiting and plugging in.
function hub(root, { label = 'Claude', size = 1.6 } = {}) {
  const g = new THREE.Group();
  const body = new THREE.Mesh(new RoundedBoxGeometry(size, size, size * 0.42, 6, 0.3), M.blue(0.45));
  g.add(body);
  const txt = new THREE.Mesh(new THREE.PlaneGeometry(size * 0.9, size * 0.3),
    new THREE.MeshBasicMaterial({ map: textTex([label], { w: 1024, h: 340, font: '600 210px IBM Plex Sans',
                                                          color: '#0b1226' }), transparent: true }));
  txt.position.z = size * 0.21 + 0.004;
  g.add(txt);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(size * 0.85, 0.035, 12, 120), M.glow(3));
  g.add(ring);
  root.add(g);
  return { g, ring };
}

SCENES.claude = (root) => {
  const centre = new THREE.Vector3(3.6, 1.75, 0);
  const h = hub(root);
  h.g.position.copy(centre);
  const orbit = new THREE.Group();
  orbit.position.copy(centre);
  orbit.rotation.x = 0.32;
  orbit.rotation.z = -0.12;
  root.add(orbit);
  const track = new THREE.Mesh(new THREE.TorusGeometry(2.45, 0.012, 8, 200), M.wire());
  track.rotation.x = Math.PI / 2;
  orbit.add(track);
  const names = ['file', 'db', 'cal', 'globe', 'chat'];
  const mods = names.map((n) => {
    const t = tile(n, { size: 0.78, depth: 0.26 });
    const prong = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.1, 0.32), M.metal());
    prong.position.z = -0.28;
    t.add(prong);
    orbit.add(t);
    return t;
  });
  const beams = mods.map(() => {
    const geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
    const line = new THREE.Line(geo, new THREE.LineBasicMaterial({ color: C.blueL, transparent: true, opacity: 0.35 }));
    orbit.add(line);
    const bead = new THREE.Mesh(new THREE.SphereGeometry(0.06, 16, 12), M.glow(5));
    orbit.add(bead);
    return { geo, bead };
  });
  return {
    cam: [0.6, 2.9, 9.0], at: [1.6, 1.5, 0],
    update(t) {
      h.g.rotation.y = -0.35 + 0.12 * wave(t);
      h.g.rotation.x = 0.04 * wave(t, 1, 0.25);
      h.ring.rotation.z = TAU * t / L;
      mods.forEach((m, i) => {
        const a = TAU * (t / L + i / mods.length);
        const r = 2.45;
        m.position.set(Math.cos(a) * r, 0.0, Math.sin(a) * r);
        m.lookAt(orbit.localToWorld(new THREE.Vector3()).clone());
        m.rotateY(Math.PI);                           // its face out, its prong towards the hub
        const b = beams[i];
        b.geo.setFromPoints([new THREE.Vector3(), m.position.clone().multiplyScalar(0.82)]);
        const u = fract(2 * t / L + i * 0.2);
        b.bead.position.copy(m.position).multiplyScalar(0.25 + 0.57 * u);
        const front = Math.sin(a) > 0.2;
        m.userData.face.material.emissiveIntensity = front ? 1.2 : 0.4;
        m.userData.face.material.color.set(front ? C.blue : C.blueL);
        m.userData.face.material.emissive.set(front ? C.blue : C.blueL);
      });
    },
  };
};

// project: the hub on the left, the n8n chain on the right, one cable between.
SCENES.project = (root) => {
  const h = hub(root, { size: 1.25 });
  h.g.position.set(2.6, 1.6, 0.3);
  h.g.rotation.y = 0.25;
  const specs = [['globe', [4.4, 2.1, -0.4]], ['spark', [5.5, 1.35, 0.1]], ['db', [6.6, 2.0, -0.6]]];
  const tiles = specs.map(([g, p], i) => {
    const t = tile(g, { lit: i === 1, size: 0.95 });
    t.position.set(...p); t.rotation.y = -0.25; root.add(t); return t;
  });
  const main = bezier([2.6, 1.6, 0.3], [3.72, 2.1, -0.4], 0.8);
  root.add(cable(main, { r: 0.055, mat: M.blue(0.25) }));
  const runs = [{ curve: main, go: pulses(root, main, { n: 3, k: 1, r: 0.09 }) }];
  [[0, 1], [1, 2]].forEach(([a, b], i) => {
    const A = tiles[a].position, B = tiles[b].position;
    const c = bezier([A.x + 0.5, A.y, A.z], [B.x - 0.5, B.y, B.z]);
    root.add(cable(c));
    runs.push({ curve: c, go: pulses(root, c, { n: 2, k: 1, phase: 0.3 + i * 0.15 }) });
  });
  return {
    cam: [0.9, 2.8, 10.6], at: [2.1, 1.5, 0], stillShift: -0.3,
    update(t) {
      h.g.position.y = 1.6 + 0.06 * wave(t);
      h.ring.rotation.z = TAU * t / L;
      tiles.forEach((x, i) => {
        x.position.y = specs[i][1][1] + 0.07 * wave(t, 1, 0.2 + i * 0.25);
        x.userData.face.material.emissiveIntensity =
          (x.userData.lit ? 1.4 : 0.4) + 2.0 * blip(t, (0.55 + i * 0.15) % 1, 0.06);
      });
      runs.forEach((r) => r.go(t));
    },
  };
};

// business: a dashboard laid on the bench, bars rising, cards hovering over it.
SCENES.business = (root) => {
  const desk = new THREE.Group();
  desk.position.set(4.0, 0.0, 0.2);
  desk.rotation.y = -0.35;
  root.add(desk);
  const slab = new THREE.Mesh(new RoundedBoxGeometry(4.2, 0.16, 2.6, 4, 0.07), M.body());
  slab.position.y = 0.08;
  desk.add(slab);
  const bars = [];
  for (let i = 0; i < 6; i++) {
    const lit = i === 4;
    const b = new THREE.Mesh(new RoundedBoxGeometry(0.36, 1, 0.36, 3, 0.06), lit ? M.blue(0.7) : M.paper());
    b.position.set(-1.6 + i * 0.58, 0, 0.35);
    desk.add(b);
    bars.push(b);
  }
  const line = new THREE.CatmullRomCurve3([-1.6, -1.0, -0.4, 0.2, 0.8, 1.4].map(
    (x, i) => new THREE.Vector3(x, 0.4 + [0.2, 0.5, 0.35, 0.8, 1.1, 1.5][i], -0.7)));
  desk.add(cable(line, { r: 0.03, mat: M.blue(0.5) }));
  const goLine = pulses(desk, line, { n: 1, k: 1, r: 0.08 });
  const cards = [['form', 'Invoice', [5.5, 2.45, -0.8]], ['chat', 'Reply', [2.6, 2.3, 0.5]],
                 ['cal', 'Booked', [4.1, 3.05, -1.4]]].map(([g, label, p], i) => {
    const tex = canvasTex(512, 300, (c, w, h) => {
      c.fillStyle = '#182540'; c.fillRect(0, 0, w, h);
      c.save(); c.translate(30, 60); drawGlyph(c, GLYPH[g], 180, 9, '#f6f8ff'); c.restore();
      c.fillStyle = '#f6f8ff'; c.font = '600 70px IBM Plex Sans'; c.textBaseline = 'middle';
      c.fillText(label, 240, 120);
      c.fillStyle = i === 0 ? '#526de6' : '#b4bdd6';
      c.fillRect(244, 180, 200, 22); c.fillRect(244, 220, 130, 18);
    });
    const card = cardMesh(1.55, 0.92, 0.07, tex);
    card.position.set(...p);
    card.rotation.set(-0.08, -0.3, 0.04 * (i - 1));
    root.add(card);
    return { card, p };
  });
  return {
    cam: [1.0, 2.9, 9.6], at: [2.0, 1.5, 0],
    update(t) {
      bars.forEach((b, i) => {
        const hgt = 0.55 + 0.35 * i + 0.28 * wave(t, 1, i * 0.12) + (i === 4 ? 0.25 : 0);
        b.scale.y = Math.max(0.2, hgt);
        b.position.y = 0.16 + b.scale.y / 2;
      });
      goLine(t);
      cards.forEach(({ card, p }, i) => {
        card.position.y = p[1] + 0.09 * wave(t, 1, i * 0.3);
        card.rotation.y = -0.3 + 0.06 * wave(t, 1, 0.1 + i * 0.27);
      });
    },
  };
};

// agents: a faceted core with three orbit rings, tools riding the rings.
function core(root, { r = 1.0 } = {}) {
  const g = new THREE.Group();
  const shell = new THREE.Mesh(new THREE.IcosahedronGeometry(r, 1),
    new THREE.MeshPhysicalMaterial({ color: 0x182540, roughness: 0.15, metalness: 0.3, clearcoat: 1,
                                     flatShading: true, transparent: true, opacity: 0.88 }));
  g.add(shell);
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(r * 1.002, 1)),
    new THREE.LineBasicMaterial({ color: new THREE.Color(C.blue).multiplyScalar(1.6) }));
  g.add(edges);
  const heart = new THREE.Mesh(new THREE.SphereGeometry(r * 0.42, 32, 24), M.glow(4));
  g.add(heart);
  const light = new THREE.PointLight(C.blue, 6, 6, 2);
  g.add(light);
  root.add(g);
  return { g, shell, edges, heart };
}

SCENES.agents = (root) => {
  const centre = new THREE.Vector3(3.8, 2.0, 0);
  const c = core(root, { r: 1.05 });
  c.g.position.copy(centre);
  const rings = [[0.4, 0.1], [1.25, -0.5], [2.1, 0.7]].map(([rx, rz], i) => {
    const g = new THREE.Group();
    g.position.copy(centre);
    g.rotation.set(rx, 0, rz);
    root.add(g);
    const rad = 1.9 + i * 0.32;
    g.add(new THREE.Mesh(new THREE.TorusGeometry(rad, 0.014, 8, 200), M.wire()));
    const t = tile(['globe', 'db', 'chat'][i], { size: 0.62, depth: 0.2, lit: i === 0 });
    g.add(t);
    const bead = new THREE.Mesh(new THREE.SphereGeometry(0.07, 16, 12), M.glow(5));
    g.add(bead);
    return { g, t, bead, rad, i };
  });
  return {
    cam: [0.8, 3.0, 9.2], at: [1.7, 1.7, 0],
    update(t) {
      c.g.rotation.y = TAU * t / L;
      c.g.rotation.x = 0.15 * wave(t);
      const beat = 1 + 0.06 * blip(t, 0.0, 0.1) + 0.06 * blip(t, 0.5, 0.1);
      c.heart.scale.setScalar(beat);
      rings.forEach(({ t: tl, bead, rad, i }) => {
        const dir = i % 2 ? -1 : 1;
        const a = dir * TAU * (t / L) + i * 2.1;
        tl.position.set(Math.cos(a) * rad, Math.sin(a) * rad, 0);
        tl.rotation.z = a + Math.PI / 2;
        const b = -dir * TAU * (2 * t / L) + i;
        bead.position.set(Math.cos(b) * rad, Math.sin(b) * rad, 0);
      });
    },
  };
};

// local: a compact GPU box on the bench, the agent core floating above it.
SCENES.local = (root) => {
  const box = new THREE.Group();
  box.position.set(4.0, 0, 0.2);
  box.rotation.y = -0.5;
  root.add(box);
  const shell = new THREE.Mesh(new RoundedBoxGeometry(2.6, 0.78, 2.6, 5, 0.12),
    new THREE.MeshPhysicalMaterial({ color: 0xb4bdd6, roughness: 0.3, metalness: 0.9, clearcoat: 0.6 }));
  shell.position.y = 0.39 + 0.04;
  box.add(shell);
  // the front: a vent grid and one blue status strip
  const vent = canvasTex(512, 160, (g, w, h) => {
    g.fillStyle = '#182540'; g.fillRect(0, 0, w, h);
    g.fillStyle = '#0b1226';
    for (let y = 18; y < h - 10; y += 22) for (let x = 18 + (y % 44 ? 11 : 0); x < w - 10; x += 22) {
      g.beginPath(); g.arc(x, y, 7, 0, TAU); g.fill();
    }
  });
  const front = new THREE.Mesh(new THREE.PlaneGeometry(2.3, 0.56),
    new THREE.MeshStandardMaterial({ map: vent, roughness: 0.7, metalness: 0.4 }));
  front.position.set(0, 0.43, 1.302);
  box.add(front);
  const strip = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.04, 0.02), M.glow(4));
  strip.position.set(0, 0.12, 1.31);
  box.add(strip);
  const c = core(root, { r: 0.72 });
  const coreAt = new THREE.Vector3(4.0, 2.65, 0.2);
  c.g.position.copy(coreAt);
  const beams = [-0.6, 0, 0.6].map((dx, i) => {
    const curve = new THREE.LineCurve3(new THREE.Vector3(4.0 + dx, 0.86, 0.2 + dx * 0.4),
                                       new THREE.Vector3(4.0 + dx * 0.3, 2.1, 0.2));
    root.add(cable(curve, { r: 0.012 }));
    return pulses(root, curve, { n: 2, k: 2, phase: i * 0.17, r: 0.055 });
  });
  return {
    cam: [0.9, 3.2, 9.0], at: [1.8, 1.6, 0],
    update(t) {
      c.g.rotation.y = TAU * t / L;
      c.g.position.y = coreAt.y + 0.1 * wave(t);
      strip.scale.x = 0.75 + 0.25 * (0.5 + 0.5 * wave(t, 2));
      beams.forEach((b) => b(t));
    },
  };
};

// channel: the two playlists as cards, standing on the bench.
SCENES.channel = (root) => {
  const specs = [['Claude', 'MCP, skills, connectors', true, [3.2, 1.55, 0.6]],
                 ['n8n', 'Workflows from zero', false, [5.5, 2.45, -1.3]]];
  const cards = specs.map(([t1, t2, lit, p], i) => {
    const tex = canvasTex(1024, 600, (g, w, h) => {
      g.fillStyle = '#182540'; g.fillRect(0, 0, w, h);
      g.save(); g.translate(60, 170); g.fillStyle = lit ? '#526de6' : '#9db0f5';
      g.beginPath(); g.moveTo(0, 0); g.lineTo(150, 90); g.lineTo(0, 180); g.closePath(); g.fill();
      g.restore();
      g.fillStyle = '#f6f8ff'; g.font = '600 120px IBM Plex Sans'; g.textBaseline = 'middle';
      g.fillText(t1, 260, 220);
      g.fillStyle = '#b4bdd6'; g.font = '500 54px IBM Plex Sans';
      g.fillText(t2, 262, 340);
      g.fillStyle = lit ? '#526de6' : '#9db0f5';
      g.fillRect(262, 440, 520, 14);
    });
    const card = cardMesh(3.0, 1.75, 0.12, tex, { mat: M.body() });
    card.position.set(...p);
    root.add(card);
    const edge = new THREE.Mesh(new RoundedBoxGeometry(3.04, 1.79, 0.06, 4, 0.08), lit ? M.blue(0.8) : M.wire());
    edge.position.z = -0.04;
    card.add(edge);
    return { card, p, i };
  });
  return {
    cam: [1.2, 2.4, 7.6], at: [2.4, 1.8, 0], stillShift: -0.5,
    update(t) {
      cards.forEach(({ card, p, i }) => {
        card.position.y = p[1] + 0.08 * wave(t, 1, i * 0.4);
        card.rotation.y = -0.32 + 0.1 * wave(t, 1, 0.15 + i * 0.4);
        card.rotation.x = -0.04 + 0.03 * wave(t, 1, 0.3 + i * 0.2);
      });
    },
  };
};

// mark: the ATLAS mark (the store's icon: a ring, an A, a blue bar).
// t < 0 is the sting: the ring draws, the A rises, the bar lights, it glides right.
SCENES.mark = (root) => {
  const g = new THREE.Group();
  root.add(g);
  const R = 1.35;
  const ringMat = M.paper();
  let ring = null;
  const setRing = (arc) => {
    if (ring) { g.remove(ring); ring.geometry.dispose(); }
    ring = new THREE.Mesh(new THREE.TorusGeometry(R, 0.1, 18, 160, Math.max(0.001, arc)), ringMat);
    ring.rotation.z = Math.PI / 2;
    g.add(ring);
  };
  setRing(TAU);
  const stroke = (a, b, mat, r = 0.11) => {
    const A = new THREE.Vector3(...a), B = new THREE.Vector3(...b);
    const len = A.distanceTo(B);
    const m = new THREE.Mesh(new THREE.CapsuleGeometry(r, len, 8, 16), mat);
    m.position.copy(A.clone().add(B).multiplyScalar(0.5));
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), B.clone().sub(A).normalize());
    m.userData.ends = [A, B];
    g.add(m);
    return m;
  };
  // A stroke drawn from its first end, as a pen would: k of its length.
  const grow = (m, k) => {
    const [A, B] = m.userData.ends;
    m.scale.set(1, Math.max(0.001, k), 1);
    m.position.copy(A).lerp(B, k / 2);
  };
  // the icon's A: from (21,45) to (32,18) to (43,45) in a 64 box, centre 32
  const s = R / 22;
  const P = (x, y) => [(x - 32) * s, (32 - y) * s, 0];
  const left = stroke(P(21, 45), P(32, 18), M.paper());
  const right = stroke(P(32, 18), P(43, 45), M.paper());
  const bar = stroke(P(25, 37), P(39, 37), M.glow(5), 0.1);
  const barLight = new THREE.PointLight(C.blue, 0, 5, 2);
  barLight.position.set(...P(32, 37));
  barLight.position.z = 0.4;
  g.add(barLight);
  const word = new THREE.Mesh(new THREE.PlaneGeometry(4.4, 0.75),
    new THREE.MeshBasicMaterial({ map: textTex(['ATLAS Academy'], { w: 1400, h: 240, font: '600 170px IBM Plex Sans' }),
                                  transparent: true }));
  root.add(word);
  const home = new THREE.Vector3(3.3, 1.95, 0);
  const centre = new THREE.Vector3(1.55, 2.25, 1.2);
  return {
    cam: [0.6, 2.6, 8.6], at: [1.5, 1.6, 0],
    update(t) {
      const sway = (u) => -0.3 + 0.18 * wave(u) ;
      if (t >= 0) {
        setRing(TAU);
        g.position.copy(home);
        g.position.y += 0.08 * wave(t, 1, 0.25);
        g.rotation.set(0.05 * wave(t, 1, 0.5), sway(t), 0);
        [left, right].forEach((m) => { grow(m, 1); m.visible = true; });
        bar.visible = true; grow(bar, 1);
        bar.material.color.set(new THREE.Color(C.blue).multiplyScalar(4 + 1.5 * blip(t, 0, 0.12)));
        barLight.intensity = 5;
        word.visible = false;
        return;
      }
      const u = (t + STING) / STING;                 // 0..1 through the sting
      setRing(TAU * easeOut(u / 0.35));
      const aL = ease((u - 0.2) / 0.14), aR = ease((u - 0.32) / 0.14);
      left.visible = aL > 0.001; grow(left, aL);
      right.visible = aR > 0.001; grow(right, aR);
      const b = ease((u - 0.47) / 0.1);
      bar.visible = b > 0.001;
      grow(bar, b);
      const flash = Math.max(0, 1 - Math.abs(u - 0.58) / 0.08);
      bar.material.color.set(new THREE.Color(C.blue).multiplyScalar(4 + 10 * flash));
      barLight.intensity = 5 * b + 20 * flash;
      const glide = ease((u - 0.72) / 0.28);
      g.position.lerpVectors(centre, home, glide);
      g.position.y += 0.08 * wave(0, 1, 0.25) * glide;
      g.rotation.set(0.05 * wave(0, 1, 0.5) * glide, (1 - glide) * (-0.9 + 0.9 * easeOut(u / 0.6)) + glide * sway(0), 0);
      word.visible = true;
      word.position.set(centre.x + glide * 2.4, centre.y - 2.0, centre.z);
      word.material.opacity = ease((u - 0.5) / 0.15) * (1 - ease((u - 0.72) / 0.14));
    },
  };
};

// outro: a big blue Subscribe button that presses itself, the store on a card.
SCENES.outro = (root) => {
  const btn = new THREE.Group();
  root.add(btn);
  const body = new THREE.Mesh(new RoundedBoxGeometry(3.6, 1.05, 0.42, 6, 0.24), M.blue(0.55));
  btn.add(body);
  const label = new THREE.Mesh(new THREE.PlaneGeometry(3.3, 0.9), new THREE.MeshBasicMaterial({
    transparent: true, map: canvasTex(1100, 300, (g, w, h) => {
      g.save(); g.translate(40, 40); g.fillStyle = '#0b1226';
      g.scale(2.2, 2.2); g.translate(50, 50);
      g.fill(new Path2D(GLYPH.bell[0].d)); g.beginPath(); g.arc(0, 28, 6, 0, TAU); g.fill();
      g.restore();
      g.fillStyle = '#0b1226'; g.font = '600 170px IBM Plex Sans'; g.textBaseline = 'middle';
      g.fillText('Subscribe', 300, h / 2 + 8);
    }) }));
  label.position.z = 0.213;
  btn.add(label);
  const ripple = new THREE.Mesh(new THREE.TorusGeometry(1, 0.02, 8, 120),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(C.blue).multiplyScalar(3), transparent: true }));
  root.add(ripple);
  const shop = cardMesh(3.4, 0.9, 0.08, textTex(['agency.storefront.test'], { w: 1200, h: 300,
    font: '600 140px IBM Plex Sans', color: '#f6f8ff' }));
  root.add(shop);
  const chips = [['heart', [5.6, 3.15, -0.6]], ['chat', [5.9, 1.55, -0.5]]].map(([gl, p]) => {
    const t = tile(gl, { size: 0.8, depth: 0.24, lit: gl === 'heart' });
    t.position.set(...p); root.add(t); return { t, p };
  });
  const at = new THREE.Vector3(3.6, 2.35, 0.2);
  return {
    cam: [0.9, 2.5, 8.8], at: [1.9, 1.7, 0],
    update(t) {
      const press = blip(t, 0.5, 0.05);
      btn.position.set(at.x, at.y + 0.06 * wave(t), at.z - 0.18 * press);
      btn.rotation.set(-0.06 + 0.04 * wave(t, 1, 0.3), -0.3 + 0.08 * wave(t, 1, 0.1), 0.02 * wave(t, 1, 0.6));
      const r = fract(t / L - 0.5);
      ripple.position.copy(btn.position);
      ripple.rotation.copy(btn.rotation);
      ripple.scale.set(1.9 + 3.5 * r, 0.7 + 1.5 * r, 1);
      ripple.material.opacity = r < 0.5 ? (1 - r * 2) * 0.9 : 0;
      shop.position.set(3.7, 1.0 + 0.05 * wave(t, 1, 0.4), 0.9);
      shop.rotation.set(-0.12, -0.28 + 0.05 * wave(t, 1, 0.2), 0);
      chips.forEach(({ t: c, p }, i) => {
        c.position.y = p[1] + 0.1 * wave(t, 1, i * 0.5);
        c.rotation.y = -0.4 + 0.2 * wave(t, 1, 0.2 + i * 0.3);
      });
    },
  };
};

// ------------------------------------------------------- topic loops
// 2026-10-11, Miro: "make the background keep changing and not stick to one
// ... change background depending on what you are talking about". One loop
// per topic, same bench, same light, same one-blue rule, subject on the right.

// A letter or a word on a transparent plane.
function label(text, { w = 1.2, h = 0.5, font = '600 170px IBM Plex Sans', color = '#f6f8ff' } = {}) {
  const tex = textTex([text], { w: 1024, h: Math.round(1024 * h / w), font, color });
  return new THREE.Mesh(new THREE.PlaneGeometry(w, h),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }));
}

// A raised dark card with a glyph and two muted lines, like a message or a record.
function noteCard(glyph, { w = 1.5, h = 0.8, accent = false, title = '' } = {}) {
  const tex = canvasTex(512, Math.round(512 * h / w), (c, cw, ch) => {
    c.fillStyle = '#182540'; c.fillRect(0, 0, cw, ch);
    c.save(); c.translate(20, ch / 2 - 80); drawGlyph(c, GLYPH[glyph], 160, 9, '#f6f8ff'); c.restore();
    if (title) {
      c.fillStyle = '#f6f8ff'; c.font = '600 64px IBM Plex Sans'; c.textBaseline = 'middle';
      c.fillText(title, 200, ch * 0.36);
    }
    c.fillStyle = accent ? '#526de6' : '#b4bdd6';
    c.fillRect(200, ch * (title ? 0.58 : 0.38), 240, 20);
    c.fillStyle = '#b4bdd6';
    c.fillRect(200, ch * (title ? 0.74 : 0.6), 160, 16);
  });
  return cardMesh(w, h, 0.06, tex);
}

// trigger: the bolt tile sending rings out, a form flying in to set it off.
SCENES.trigger = (root) => {
  const at = new THREE.Vector3(3.5, 1.7, 0);
  const t0 = tile('bolt', { size: 1.35, depth: 0.4, lit: true });
  t0.position.copy(at); t0.rotation.y = -0.25; root.add(t0);
  const rings = [0, 1, 2].map(() => {
    const m = new THREE.Mesh(new THREE.TorusGeometry(1, 0.025, 8, 120),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(C.blue).multiplyScalar(2.5), transparent: true }));
    m.position.copy(at); m.rotation.y = -0.25; root.add(m); return m;
  });
  const card = noteCard('form', { w: 1.1, h: 0.62, accent: true });
  root.add(card);
  const outs = [['chat', [5.6, 2.35, -0.6]], ['db', [5.6, 1.0, 0.3]]].map(([g, p]) => {
    const t = tile(g, { size: 0.8, depth: 0.26 }); t.position.set(...p); t.rotation.y = -0.3; root.add(t);
    const c = bezier([at.x + 0.7, at.y, at.z], [p[0] - 0.42, p[1], p[2]]);
    root.add(cable(c));
    return { t, go: pulses(root, c, { n: 1, k: 1, phase: 0.62, r: 0.07 }), p };
  });
  return {
    cam: [0.8, 2.7, 8.8], at: [2.0, 1.45, 0],
    update(t) {
      const u = fract(t / L);
      // the form flies in over the first 40% and lands in the tile
      const fly = ease(u / 0.4);
      card.position.set(at.x + 2.6 - 2.6 * fly, at.y + 1.6 - 1.6 * fly + 0.25 * Math.sin(Math.PI * fly), at.z + 0.6 * (1 - fly));
      card.rotation.set(-0.2 * (1 - fly), -0.6 + 0.35 * fly, 0.25 * (1 - fly));
      card.scale.setScalar(Math.max(0.001, 1 - ease((u - 0.38) / 0.06)) * (u < 0.95 ? 1 : ease((u - 0.95) / 0.05)));
      const hit = blip(t, 0.42, 0.07);
      t0.userData.face.material.emissiveIntensity = 1.4 + 3.0 * hit;
      t0.position.y = at.y + 0.06 * wave(t);
      rings.forEach((m, i) => {
        const r = fract(u - 0.42 - i * 0.09);
        m.scale.setScalar(0.85 + 2.4 * r);
        m.material.opacity = r < 0.55 ? (1 - r / 0.55) * 0.8 : 0;
      });
      outs.forEach(({ t: tl, go, p }, i) => { go(t); tl.position.y = p[1] + 0.06 * wave(t, 1, 0.3 + i * 0.4); });
    },
  };
};

// api: a request goes out to a server rack, the answer comes back.
SCENES.api = (root) => {
  const g = tile('globe', { size: 1.1, lit: true });
  g.position.set(2.7, 1.7, 0.2); g.rotation.y = -0.2; root.add(g);
  const rack = new THREE.Group();
  rack.position.set(5.5, 0, -0.4); rack.rotation.y = -0.45; root.add(rack);
  const leds = [];
  for (let i = 0; i < 3; i++) {
    const slab = new THREE.Mesh(new RoundedBoxGeometry(1.5, 0.5, 1.1, 4, 0.06), M.body());
    slab.position.y = 0.32 + i * 0.58; rack.add(slab);
    const led = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.05, 0.02), M.glow(3));
    led.position.set(-0.3, 0.32 + i * 0.58, 0.56); rack.add(led); leds.push(led);
  }
  const out = bezier([3.3, 1.95, 0.2], [4.85, 1.75, -0.1], 0.8);
  const back = bezier([4.85, 1.2, -0.1], [3.3, 1.45, 0.2], 0.8);
  // the return cable runs right-to-left, so bend it the other way
  root.add(cable(out)); root.add(cable(back));
  const goOut = pulses(root, out, { n: 1, k: 1, phase: 0.0, r: 0.08 });
  const goBack = pulses(root, back, { n: 1, k: 1, phase: 0.5, r: 0.08 });
  const get = label('GET', { w: 0.9, h: 0.38, color: '#9db0f5' });
  get.position.set(4.0, 2.45, 0.1); root.add(get);
  const ok = label('200', { w: 0.9, h: 0.38, color: '#526de6' });
  ok.position.set(4.0, 0.75, 0.1); root.add(ok);
  return {
    cam: [0.8, 2.6, 8.8], at: [2.0, 1.4, 0],
    update(t) {
      goOut(t); goBack(t);
      leds.forEach((l, i) => { l.scale.x = 0.4 + 0.6 * (0.5 + 0.5 * wave(t, 3, i * 0.21)); });
      g.userData.face.material.emissiveIntensity = 1.4 + 2.4 * blip(t, 0.97, 0.06);
      get.material.opacity = 0.35 + 0.65 * blip(t, 0.25, 0.2);
      ok.material.opacity = 0.35 + 0.65 * blip(t, 0.75, 0.2);
      g.position.y = 1.7 + 0.06 * wave(t);
    },
  };
};

// message: chat cards rising one after another, the newest lit.
SCENES.message = (root) => {
  const N = 4;
  const cards = Array.from({ length: N }, (_, i) => {
    const c = noteCard(i % 2 ? 'chat' : 'bell', { w: 1.9, h: 0.72, accent: i === 0 });
    root.add(c); return c;
  });
  const side = tile('chat', { size: 0.9, depth: 0.28, lit: true });
  side.position.set(5.9, 1.6, -0.8); side.rotation.y = -0.4; root.add(side);
  return {
    cam: [0.8, 2.6, 8.8], at: [2.0, 1.5, 0],
    update(t) {
      cards.forEach((c, i) => {
        const u = fract(t / L + i / N);              // 0 = enters at the bottom, 1 = leaves at the top
        c.position.set(3.7 + 0.25 * Math.sin(TAU * u), 0.35 + 3.0 * u, 0.2 - 0.6 * u);
        c.rotation.set(-0.1, -0.3 + 0.08 * Math.sin(TAU * (u + i * 0.2)), 0.03 * (i % 2 ? 1 : -1));
        const s = Math.min(1, u / 0.12, (1 - u) / 0.12);
        c.scale.setScalar(Math.max(0.001, ease(s)));
      });
      side.position.y = 1.6 + 0.08 * wave(t);
      side.userData.face.material.emissiveIntensity = 1.2 + 2.0 * Math.max(...[0, 0.25, 0.5, 0.75].map((ph) => blip(t, ph, 0.05)));
    },
  };
};

// code: a reference card of code, the lit line walking down it, braces floating.
SCENES.code = (root) => {
  const rows = 9;
  const tex = canvasTex(1024, 760, (c, w, h) => {
    c.fillStyle = '#182540'; c.fillRect(0, 0, w, h);
    const widths = [520, 380, 640, 300, 560, 460, 700, 260, 420];
    for (let i = 0; i < rows; i++) {
      const y = 60 + i * 76;
      c.fillStyle = '#9db0f5'; c.fillRect(60, y, 40, 26);
      c.fillStyle = i % 3 === 0 ? '#f6f8ff' : '#b4bdd6';
      c.fillRect(130 + (i % 4 ? 40 : 0), y, widths[i], 26);
    }
  });
  const card = cardMesh(3.0, 2.2, 0.08, tex);
  card.position.set(3.9, 1.9, -0.2); card.rotation.set(-0.08, -0.32, 0.02); root.add(card);
  const band = new THREE.Mesh(new THREE.PlaneGeometry(2.7, 0.16),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(C.blue).multiplyScalar(1.6), transparent: true, opacity: 0.45, depthWrite: false }));
  card.add(band);
  const braces = [-1, 1].map((s) => {
    const b = face('brace', 0.9, C.blue);
    b.material.emissiveIntensity = 1.4;
    b.position.set(3.9 + s * 2.0, 1.9, 0.5); root.add(b);
    if (s < 0) b.scale.x = 1; return b;
  });
  return {
    cam: [0.8, 2.6, 8.8], at: [2.0, 1.55, 0],
    update(t) {
      const k = Math.floor(fract(t / L) * rows);
      const y = (0.5 - (60 + k * 76 + 13) / 760) * 2.2 * 0.94;
      band.position.set(0, y, 0.046);
      card.position.y = 1.9 + 0.06 * wave(t);
      card.rotation.y = -0.32 + 0.05 * wave(t, 1, 0.2);
      braces.forEach((b, i) => { b.position.y = 1.9 + 0.12 * wave(t, 1, i * 0.5); b.rotation.y = -0.3 + 0.2 * wave(t, 1, 0.25 + i * 0.5); });
    },
  };
};

// memory: cards filing into a stack of records; the stack glows as it takes one.
SCENES.memory = (root) => {
  const base = new THREE.Vector3(4.2, 0, 0);
  const disks = [0, 1, 2, 3].map((i) => {
    const d = new THREE.Mesh(new THREE.CylinderGeometry(0.95, 0.95, 0.28, 64), i === 3 ? M.blue(0.5) : M.body());
    d.position.set(base.x, 0.2 + i * 0.36, base.z); root.add(d); return d;
  });
  const cards = [0, 1].map((i) => { const c = noteCard(i ? 'file' : 'chat', { w: 1.1, h: 0.62 }); root.add(c); return c; });
  return {
    cam: [0.8, 2.6, 8.8], at: [2.0, 1.4, 0],
    update(t) {
      disks.forEach((d, i) => { d.rotation.y = TAU * t / L * (i % 2 ? 1 : -1); });
      disks[3].material.emissiveIntensity = 0.4 + 1.6 * Math.max(blip(t, 0.42, 0.06), blip(t, 0.92, 0.06));
      cards.forEach((c, i) => {
        const u = fract(t / L - i * 0.5);
        const f = ease(u / 0.42);
        c.position.set(base.x - 2.3 + 2.3 * f, 2.6 - 1.1 * f + 0.4 * Math.sin(Math.PI * f), 0.6 - 0.6 * f);
        c.rotation.set(-0.3 * f, -0.5 + 0.5 * f, 0.15 * (1 - f));
        const s = Math.min(1, u / 0.06, Math.max(0, (0.46 - u) / 0.06));
        c.scale.setScalar(Math.max(0.001, s));
      });
    },
  };
};

// tools: tool tiles plugging into a strip, each lighting as it seats.
SCENES.tools = (root) => {
  const strip = new THREE.Mesh(new RoundedBoxGeometry(4.2, 0.3, 1.0, 4, 0.1), M.body());
  strip.position.set(3.7, 0.35, 0); strip.rotation.y = -0.3; root.add(strip);
  const names = ['wrench', 'gear', 'globe', 'file'];
  const tools = names.map((n, i) => {
    const t = tile(n, { size: 0.82, depth: 0.26, lit: true });
    root.add(t);
    const x = -1.5 + i * 1.0;
    const home = new THREE.Vector3(x, 0, 0).applyAxisAngle(new THREE.Vector3(0, 1, 0), -0.3).add(new THREE.Vector3(3.7, 0, 0));
    const sock = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.04, 0.18), M.glow(2));
    sock.position.set(home.x, 0.51, home.z + 0.02); sock.rotation.y = -0.3; root.add(sock);
    return { t, home, sock, i };
  });
  return {
    cam: [0.8, 2.7, 8.6], at: [2.0, 1.3, 0],
    update(t) {
      tools.forEach(({ t: tl, home, sock, i }) => {
        const u = fract(t / L - i * 0.18);
        const seat = ease(u / 0.25) * (1 - ease((u - 0.7) / 0.25));
        tl.position.set(home.x, 0.95 + 1.3 * (1 - seat) + 0.05 * wave(t, 1, i * 0.3), home.z);
        tl.rotation.set(0, -0.3 + 0.6 * (1 - seat), 0);
        tl.userData.face.material.emissiveIntensity = 0.4 + 1.8 * seat;
        tl.userData.rim.emissiveIntensity = 0.1 + 0.9 * seat;
        sock.material.color.set(new THREE.Color(C.blue).multiplyScalar(0.5 + 3.5 * seat));
      });
    },
  };
};

// server: a terminal monolith, lines scrolling up, the prompt blinking.
SCENES.server = (root) => {
  const lines = 24;
  const tex = canvasTex(512, 1024, (c, w, h) => {
    c.fillStyle = '#182540'; c.fillRect(0, 0, w, h);
    for (let i = 0; i < lines; i++) {
      const y = 20 + i * (h / lines);
      c.fillStyle = i % 5 === 0 ? '#526de6' : '#9db0f5';
      c.fillRect(30, y, 22, 14);
      c.fillStyle = i % 5 === 0 ? '#f6f8ff' : '#b4bdd6';
      c.fillRect(66, y, 120 + ((i * 97) % 260), 14);
    }
  });
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(1, 0.5);
  const body = new THREE.Mesh(new RoundedBoxGeometry(1.9, 3.0, 0.7, 5, 0.12), M.body());
  body.position.set(4.3, 1.55, -0.2); body.rotation.y = -0.35; root.add(body);
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 2.5),
    new THREE.MeshBasicMaterial({ map: tex }));
  screen.position.z = 0.352; body.add(screen);
  const cursor = new THREE.Mesh(new THREE.PlaneGeometry(0.12, 0.05), M.glow(4));
  cursor.position.set(-0.55, -1.1, 0.356); body.add(cursor);
  const side = tile('term', { size: 0.8, depth: 0.26, lit: true });
  side.position.set(2.6, 2.4, 0.4); side.rotation.y = -0.15; root.add(side);
  return {
    cam: [0.8, 2.6, 8.6], at: [2.0, 1.5, 0],
    update(t) {
      tex.offset.y = -fract(t / L) * 0.5 * 2;     // two screens of lines per loop, periodic
      cursor.visible = fract(t * 1.5) < 0.6;
      body.rotation.y = -0.35 + 0.05 * wave(t);
      side.position.y = 2.4 + 0.08 * wave(t, 1, 0.3);
    },
  };
};

// cloud: a cloud above and a box on the bench, data both ways between them.
SCENES.cloud = (root) => {
  const cl = new THREE.Group();
  [[0, 0, 0, 0.62], [0.6, 0.1, 0, 0.5], [-0.6, -0.05, 0, 0.48], [0.25, 0.42, -0.1, 0.48], [-0.3, 0.35, 0.1, 0.42]]
    .forEach(([x, y, z, r]) => { const s = new THREE.Mesh(new THREE.SphereGeometry(r, 40, 28), M.paper()); s.position.set(x, y, z); cl.add(s); });
  cl.position.set(3.1, 2.75, -0.4); root.add(cl);
  const box = new THREE.Mesh(new RoundedBoxGeometry(1.7, 0.6, 1.4, 5, 0.1),
    new THREE.MeshPhysicalMaterial({ color: 0xb4bdd6, roughness: 0.3, metalness: 0.9, clearcoat: 0.6 }));
  box.position.set(5.3, 0.36, 0.3); box.rotation.y = -0.4; root.add(box);
  const strip = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.04, 0.02), M.glow(4));
  strip.position.set(0, -0.1, 0.71); box.add(strip);
  const down = new THREE.CatmullRomCurve3([new THREE.Vector3(3.6, 2.3, -0.3), new THREE.Vector3(4.6, 1.6, 0), new THREE.Vector3(5.1, 0.75, 0.3)]);
  const up = new THREE.CatmullRomCurve3([new THREE.Vector3(5.5, 0.75, 0.2), new THREE.Vector3(4.9, 1.9, -0.2), new THREE.Vector3(3.9, 2.55, -0.4)]);
  root.add(cable(down)); root.add(cable(up));
  const g1 = pulses(root, down, { n: 2, k: 1, r: 0.07 }), g2 = pulses(root, up, { n: 2, k: 1, phase: 0.25, r: 0.07 });
  const lc = label('cloud', { w: 1.2, h: 0.36, color: '#9db0f5', font: '600 150px IBM Plex Sans' });
  lc.position.set(3.1, 1.75, 0.2); root.add(lc);
  const ll = label('local', { w: 1.2, h: 0.36, color: '#526de6', font: '600 150px IBM Plex Sans' });
  ll.position.set(5.3, 1.05, 0.9); root.add(ll);
  return {
    cam: [0.8, 2.6, 8.8], at: [2.0, 1.5, 0],
    update(t) {
      cl.position.y = 2.75 + 0.1 * wave(t); cl.rotation.y = 0.15 * wave(t, 1, 0.25);
      g1(t); g2(t);
      strip.scale.x = 0.7 + 0.3 * (0.5 + 0.5 * wave(t, 2));
    },
  };
};

// security: a padlock that closes, a ring of light as it locks, a shield behind.
SCENES.security = (root) => {
  const at = new THREE.Vector3(3.5, 1.35, 0);
  const lock = new THREE.Group(); lock.position.copy(at); root.add(lock);
  const body = new THREE.Mesh(new RoundedBoxGeometry(1.6, 1.25, 0.6, 5, 0.16), M.blue(0.35));
  lock.add(body);
  const hole = new THREE.Mesh(new THREE.CylinderGeometry(0.11, 0.11, 0.05, 24), new THREE.MeshStandardMaterial({ color: C.ink }));
  hole.rotation.x = Math.PI / 2; hole.position.set(0, 0.06, 0.31); lock.add(hole);
  const shackle = new THREE.Mesh(new THREE.TorusGeometry(0.5, 0.11, 18, 64, Math.PI), M.metal());
  lock.add(shackle);
  const legs = [-0.5, 0.5].map((x) => { const l = new THREE.Mesh(new THREE.CylinderGeometry(0.11, 0.11, 0.5, 18), M.metal()); l.position.x = x; lock.add(l); return l; });
  const shieldShape = new THREE.Shape();
  shieldShape.moveTo(0, 1.3); shieldShape.lineTo(1.1, 0.9); shieldShape.lineTo(1.0, -0.3);
  shieldShape.quadraticCurveTo(0.6, -1.1, 0, -1.4); shieldShape.quadraticCurveTo(-0.6, -1.1, -1.0, -0.3);
  shieldShape.lineTo(-1.1, 0.9); shieldShape.lineTo(0, 1.3);
  const shield = new THREE.Mesh(new THREE.ExtrudeGeometry(shieldShape, { depth: 0.18, bevelEnabled: true, bevelSize: 0.05, bevelThickness: 0.05 }), M.body());
  shield.position.set(at.x + 0.9, at.y + 0.9, -1.4); shield.scale.setScalar(1.5); root.add(shield);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(1, 0.02, 8, 120),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(C.blue).multiplyScalar(2.5), transparent: true }));
  ring.position.copy(at); root.add(ring);
  return {
    cam: [0.8, 2.6, 8.6], at: [2.0, 1.5, 0],
    update(t) {
      const u = fract(t / L);
      const closed = ease((u - 0.2) / 0.15) * (1 - ease((u - 0.8) / 0.15));
      const lift = 0.5 * (1 - closed);
      shackle.position.y = 0.62 + 0.25 + lift; legs.forEach((l) => { l.position.y = 0.62 + lift; });
      shackle.rotation.y = 0.6 * (1 - closed);
      legs[1].position.x = 0.5 * Math.cos(0.6 * (1 - closed)); legs[1].position.z = -0.5 * Math.sin(0.6 * (1 - closed));
      body.material.emissiveIntensity = 0.3 + 1.2 * blip(t, 0.35, 0.06) + 0.25 * closed;
      const r = fract(u - 0.35);
      ring.scale.setScalar(1.0 + 2.2 * r); ring.material.opacity = r < 0.5 ? (1 - r * 2) * 0.8 : 0;
      lock.rotation.y = -0.35 + 0.08 * wave(t); lock.position.y = at.y + 0.05 * wave(t, 1, 0.3);
      shield.rotation.y = -0.4 + 0.05 * wave(t, 1, 0.2);
    },
  };
};

// quiz: A, B, C tiles, a light passing over them, a question mark above.
SCENES.quiz = (root) => {
  const tiles = ['A', 'B', 'C'].map((ch, i) => {
    const g = new THREE.Group();
    const b = new THREE.Mesh(new RoundedBoxGeometry(1.05, 1.05, 0.32, 5, 0.2), M.body()); g.add(b);
    const rim = M.blue(0.1);
    const band = new THREE.Mesh(new RoundedBoxGeometry(1.08, 1.08, 0.1, 5, 0.2), rim); g.add(band);
    const l = label(ch, { w: 0.8, h: 0.8, font: '600 700px IBM Plex Sans' }); l.position.z = 0.17; g.add(l);
    g.position.set(2.9 + i * 1.35, 1.25 + (i === 1 ? 0.25 : 0), -0.2 * i);
    g.rotation.y = -0.25; root.add(g);
    return { g, rim, l, i };
  });
  const q = label('?', { w: 1.3, h: 1.3, font: '600 900px IBM Plex Sans', color: '#526de6' });
  q.position.set(4.3, 2.95, -0.6); root.add(q);
  return {
    cam: [0.8, 2.6, 8.8], at: [2.0, 1.55, 0],
    update(t) {
      tiles.forEach(({ g, rim, i }) => {
        const on = blip(t, 0.15 + i * 0.3, 0.1);
        rim.emissiveIntensity = 0.1 + 1.6 * on;
        g.position.y = 1.25 + (i === 1 ? 0.25 : 0) + 0.12 * on + 0.04 * wave(t, 1, i * 0.3);
        g.rotation.y = -0.25 + 0.06 * wave(t, 1, 0.1 + i * 0.25);
      });
      q.position.y = 2.95 + 0.1 * wave(t); q.rotation.z = 0.08 * wave(t, 1, 0.25);
    },
  };
};

// recap: a checklist card whose boxes tick themselves in turn.
SCENES.recap = (root) => {
  const rows = 4;
  const tex = canvasTex(800, 900, (c, w, h) => {
    c.fillStyle = '#182540'; c.fillRect(0, 0, w, h);
    for (let i = 0; i < rows; i++) {
      const y = 110 + i * 200;
      c.strokeStyle = '#b4bdd6'; c.lineWidth = 10; c.strokeRect(70, y, 110, 110);
      c.fillStyle = '#b4bdd6'; c.fillRect(240, y + 25, 360 - (i % 2) * 90, 26);
      c.fillRect(240, y + 70, 220, 18);
    }
  });
  const card = cardMesh(2.2, 2.5, 0.07, tex);
  card.position.set(4.0, 1.75, -0.1); card.rotation.set(-0.06, -0.3, 0.02); root.add(card);
  const ticks = Array.from({ length: rows }, (_, i) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(0.3, 0.3), M.glow(2.5));
    const y = (0.5 - (110 + i * 200 + 55) / 900) * 2.5 * 0.94;
    m.position.set((-0.5 + (70 + 55) / 800) * 2.2 * 0.94, y, 0.042);
    card.add(m); return m;
  });
  return {
    cam: [0.8, 2.6, 8.6], at: [2.0, 1.55, 0],
    update(t) {
      const u = fract(t / L);
      ticks.forEach((m, i) => {
        const s = ease((u - 0.1 - i * 0.14) / 0.06) * (1 - ease((u - 0.88) / 0.08));
        m.scale.setScalar(Math.max(0.001, s));
      });
      card.position.y = 1.75 + 0.06 * wave(t);
      card.rotation.y = -0.3 + 0.05 * wave(t, 1, 0.2);
    },
  };
};

// ------------------------------------------------- the 2026-10-18 series
// AI Explained, AI Security, Where AI Is Going (tutorial_series_ai.py). Each
// is a series loop and its still, and doubles as a topic (tutorial_design
// TOPICS). Same bench, light and one-blue rule; no text, no logos, nothing red.

// explained: a small network of layers inside nested glass boxes -- the
// "black box", opened. A signal runs left to right, layer by layer.
SCENES.explained = (root) => {
  const at = new THREE.Vector3(3.9, 1.75, 0);
  const net = new THREE.Group(); net.position.copy(at); net.rotation.y = -0.38; root.add(net);
  // the nested boxes: three glass shells, each a step smaller, edges in light blue
  const shells = [[3.6, 2.6, 2.0], [2.75, 1.95, 1.45], [1.9, 1.3, 0.9]].map(([w, h, d], i) => {
    const g = new THREE.Group();
    g.add(new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 4, 0.08),
      new THREE.MeshPhysicalMaterial({ color: C.body, roughness: 0.2, metalness: 0.1,
                                       transparent: true, opacity: 0.07 + 0.04 * i,
                                       depthWrite: false })));
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(w, h, d)),
      new THREE.LineBasicMaterial({ color: new THREE.Color(i === 2 ? C.blue : C.blueL)
                                     .multiplyScalar(i === 2 ? 1.4 : 0.55),
                                    transparent: true, opacity: 0.9 }));
    g.add(edges);
    net.add(g);
    return { g, edges };
  });
  // the layers: 3, 4, 4, 2 nodes, columns along x
  const counts = [3, 4, 4, 2];
  const xs = [-1.35, -0.45, 0.45, 1.35];
  const layers = counts.map((n, li) => Array.from({ length: n }, (_, k) => {
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.13, 28, 20), M.blue(0.2));
    m.position.set(xs[li], (k - (n - 1) / 2) * 0.48, 0);
    net.add(m);
    return m;
  }));
  // every node wired to every node of the next layer: thin, quiet lines
  const pts = [];
  for (let li = 0; li < layers.length - 1; li++)
    for (const a of layers[li]) for (const b of layers[li + 1]) pts.push(a.position, b.position);
  net.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: C.blueL, transparent: true, opacity: 0.28 })));
  // a few signals: beads riding one wire per layer gap, all in step
  const paths = [[0, 1, 1, 2], [1, 2, 3, 1], [2, 0, 1, 0]];
  const beads = [];
  paths.forEach((route) => {
    for (let li = 0; li < 3; li++) {
      const a = layers[li][route[li] % counts[li]].position;
      const b = layers[li + 1][route[li + 1] % counts[li + 1]].position;
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.055, 16, 12), M.glow(5));
      net.add(m);
      beads.push({ m, a, b, li });
    }
  });
  return {
    cam: [0.8, 2.7, 8.8], at: [2.0, 1.6, 0], stillShift: 0.9,
    update(t) {
      const u = fract(t / L);
      // the wave: layer li fires at phase 0.12 + 0.2 * li; twice a loop
      layers.forEach((col, li) => col.forEach((m) => {
        const hit = blip(t, 0.12 + 0.2 * li, 0.07) + blip(t, 0.62 + 0.2 * li, 0.07);
        m.material.emissiveIntensity = 0.2 + 2.4 * Math.min(1, hit);
        m.scale.setScalar(1 + 0.25 * Math.min(1, hit));
      }));
      beads.forEach(({ m, a, b, li }) => {
        // half a loop per pass; a bead travels its gap between two layers' flashes
        const p = fract(2 * u);
        const s = clamp((p - (0.24 + 0.4 * li) / 2) / 0.1);
        m.position.lerpVectors(a, b, s);
        m.visible = s > 0 && s < 1;
      });
      shells.forEach(({ g }, i) => {
        g.rotation.y = 0.06 * wave(t, 1, i * 0.18);
        g.rotation.x = 0.03 * wave(t, 1, 0.3 + i * 0.12);
      });
      net.position.y = at.y + 0.05 * wave(t);
      net.rotation.y = -0.38 + 0.07 * wave(t, 1, 0.15);
    },
  };
};

// vault (the AI Security series): a shield standing on the bench, a key
// sliding into its lock and turning; the shield's rim lights as it locks.
SCENES.vault = (root) => {
  const at = new THREE.Vector3(4.0, 1.85, 0);
  const g = new THREE.Group(); g.position.copy(at); g.rotation.y = -0.42; root.add(g);
  const s = new THREE.Shape();
  s.moveTo(0, 1.35); s.quadraticCurveTo(0.6, 1.05, 1.15, 1.05); s.lineTo(1.1, -0.15);
  s.quadraticCurveTo(0.95, -0.95, 0, -1.45); s.quadraticCurveTo(-0.95, -0.95, -1.1, -0.15);
  s.lineTo(-1.15, 1.05); s.quadraticCurveTo(-0.6, 1.05, 0, 1.35);
  const shield = new THREE.Mesh(new THREE.ExtrudeGeometry(s, { depth: 0.22, bevelEnabled: true,
    bevelSize: 0.06, bevelThickness: 0.06, bevelSegments: 4, curveSegments: 24 }), M.body());
  shield.position.z = -0.11;
  g.add(shield);
  // the rim: the shield's outline as a blue line just in front of its face
  const rimPts = s.getPoints(48).map((p) => new THREE.Vector3(p.x * 0.92, p.y * 0.92, 0.19));
  const rimMat = new THREE.LineBasicMaterial({ color: new THREE.Color(C.blue).multiplyScalar(2.2),
                                               transparent: true, opacity: 0.35 });
  g.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(rimPts), rimMat));
  // the lock plate and its keyhole
  const plate = new THREE.Mesh(new THREE.CylinderGeometry(0.36, 0.36, 0.08, 48), M.metal());
  plate.material.emissive = new THREE.Color(C.blue);
  plate.rotation.x = Math.PI / 2; plate.position.set(0, 0.05, 0.2); g.add(plate);
  const hole = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.02, 24),
    new THREE.MeshStandardMaterial({ color: C.ink }));
  hole.rotation.x = Math.PI / 2; hole.position.set(0, 0.09, 0.245); g.add(hole);
  const slot = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.16, 0.02),
    new THREE.MeshStandardMaterial({ color: C.ink }));
  slot.position.set(0, -0.02, 0.245); g.add(slot);
  // the key: a ring bow, a shaft and two bits, pointing into the lock (-z)
  const key = new THREE.Group();
  const bow = new THREE.Mesh(new THREE.TorusGeometry(0.22, 0.06, 16, 48), M.blue(0.5));
  bow.position.z = 0.95; bow.rotation.y = Math.PI / 2; key.add(bow);
  const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.045, 0.95, 18), M.blue(0.5));
  shaft.rotation.x = Math.PI / 2; shaft.position.z = 0.42; key.add(shaft);
  [0.08, 0.22].forEach((z, i) => {
    const bit = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.12 + 0.04 * i, 0.08), M.blue(0.5));
    bit.position.set(0, -0.08 - 0.02 * i, z); key.add(bit);
  });
  // the key comes in at an angle, so the camera sees it in profile
  const KEY_YAW = 1.05, KEY_IN = new THREE.Vector3(0, 0.05, 0.25);
  const keyDir = new THREE.Vector3(Math.sin(KEY_YAW), 0, Math.cos(KEY_YAW));
  key.rotation.y = KEY_YAW;
  g.add(key);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(1, 0.018, 8, 120),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(C.blue).multiplyScalar(2.5), transparent: true }));
  ring.position.set(0, 0.05, 0.25); g.add(ring);
  return {
    cam: [0.8, 2.7, 8.8], at: [2.0, 1.6, 0], stillShift: 0.95,
    update(t) {
      const u = fract(t / L);
      // in (0.08-0.3), turn (0.32-0.42), hold, turn back (0.72-0.8), out (0.82-0.98)
      const inn = ease((u - 0.08) / 0.22) * (1 - ease((u - 0.82) / 0.16));
      const turn = ease((u - 0.32) / 0.1) * (1 - ease((u - 0.72) / 0.08));
      key.position.copy(KEY_IN).addScaledVector(keyDir, 1.15 * (1 - inn));
      // bow flat while it travels; it turns upright, side on to us, to lock
      key.rotation.z = (Math.PI / 2) * (1 - turn);
      rimMat.opacity = 0.3 + 0.6 * turn + 0.5 * blip(t, 0.42, 0.05);
      plate.material.emissiveIntensity = 0.6 * turn;
      const r = fract(u - 0.42);
      ring.scale.setScalar(0.4 + 2.6 * r);
      ring.material.opacity = r < 0.45 ? (1 - r / 0.45) * 0.75 : 0;
      ring.visible = ring.material.opacity > 0.005;   // a spent ring must not draw dark
      g.position.y = at.y + 0.05 * wave(t, 1, 0.3);
      g.rotation.y = -0.42 + 0.06 * wave(t, 1, 0.1);
    },
  };
};

// future: a path running out across the bench to a horizon of light, markers
// drifting towards us along it -- where things are heading.
SCENES.future = (root) => {
  const path = new THREE.CatmullRomCurve3([
    new THREE.Vector3(3.2, 0.012, 4.2), new THREE.Vector3(4.2, 0.012, 1.0),
    new THREE.Vector3(3.9, 0.012, -3.0), new THREE.Vector3(5.2, 0.012, -9.0),
    new THREE.Vector3(6.6, 0.012, -18.0)]);
  // the path itself: a flat ribbon, wide near, narrowing with distance
  const N = 160, pos = [], idx = [];
  for (let i = 0; i <= N; i++) {
    const p = path.getPointAt(i / N), tn = path.getTangentAt(i / N);
    const side = new THREE.Vector3(-tn.z, 0, tn.x).normalize().multiplyScalar(0.55 * (1 - 0.55 * i / N));
    pos.push(p.x + side.x, p.y, p.z + side.z, p.x - side.x, p.y, p.z - side.z);
    if (i < N) idx.push(2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 1, 2 * i + 3, 2 * i + 2);
  }
  const rg = new THREE.BufferGeometry();
  rg.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  rg.setIndex(idx); rg.computeVertexNormals();
  root.add(new THREE.Mesh(rg, new THREE.MeshStandardMaterial({ color: C.body, roughness: 0.75,
    metalness: 0.1, envMapIntensity: 0.3, side: THREE.DoubleSide })));
  // its two edges, light blue
  [1, -1].forEach((sgn) => {
    const e = [];
    for (let i = 0; i <= N; i++) {
      const p = path.getPointAt(i / N), tn = path.getTangentAt(i / N);
      const side = new THREE.Vector3(-tn.z, 0, tn.x).normalize()
        .multiplyScalar(sgn * 0.55 * (1 - 0.55 * i / N));
      e.push(new THREE.Vector3(p.x + side.x, 0.02, p.z + side.z));
    }
    root.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(e),
      new THREE.LineBasicMaterial({ color: C.blueL, transparent: true, opacity: 0.55 })));
  });
  // the horizon: a low disc of blue light where the path ends, a thin bright line
  const sun = new THREE.Mesh(new THREE.CircleGeometry(2.6, 96, 0, Math.PI), M.glow(1.0));
  sun.position.set(7.4, 0, -20.5); sun.rotation.y = -0.2; root.add(sun);
  const halo = new THREE.Mesh(new THREE.TorusGeometry(3.5, 0.045, 8, 160, Math.PI), M.glow(2.0));
  halo.position.copy(sun.position); halo.rotation.y = -0.2; root.add(halo);
  // the horizon line fades out at both ends, so it never crosses the slides' text
  const fade = canvasTex(1024, 8, (c, w, h) => {
    const gr = c.createLinearGradient(0, 0, w, 0);
    gr.addColorStop(0, 'rgba(246,248,255,0)'); gr.addColorStop(0.5, 'rgba(246,248,255,1)');
    gr.addColorStop(1, 'rgba(246,248,255,0)');
    c.fillStyle = gr; c.fillRect(0, 0, w, h);
  });
  const line = new THREE.Mesh(new THREE.PlaneGeometry(26, 0.07), new THREE.MeshBasicMaterial({
    map: fade, transparent: true, depthWrite: false,
    color: new THREE.Color(C.blue).multiplyScalar(3) }));
  line.position.set(7.4, 0.04, -20.4); line.rotation.y = -0.2; root.add(line);
  // markers: small posts along the path, drifting towards us, one per lap each
  const n = 7;
  const posts = Array.from({ length: n }, (_, i) => {
    const m = new THREE.Mesh(new RoundedBoxGeometry(0.22, 0.42, 0.22, 3, 0.06),
      i === 3 ? M.blue(0.8) : M.body());
    root.add(m);
    return m;
  });
  // beads running out along the path, away from us
  const beads = pulses(root, new THREE.CatmullRomCurve3(path.points.map(
    (p) => new THREE.Vector3(p.x, 0.12, p.z))), { n: 3, k: 1, r: 0.07 });
  return {
    cam: [0.6, 2.9, 9.2], at: [2.2, 1.0, -2.0], stillShift: 0.7,
    update(t) {
      posts.forEach((m, i) => {
        // u runs from far (1) to near (0) once a loop
        const v = 1 - fract(t / L + i / n);
        const p = path.getPointAt(v), tn = path.getTangentAt(v);
        const side = new THREE.Vector3(-tn.z, 0, tn.x).normalize()
          .multiplyScalar((i % 2 ? 1 : -1) * 0.7 * (1 - 0.55 * v));
        m.position.set(p.x + side.x, 0.21, p.z + side.z);
        // fade in far away and out as they pass the camera: scale, never a pop
        m.scale.setScalar(Math.max(0.001, Math.min(1, (1 - v) * 6, v * 8)));
      });
      beads(t);
      sun.scale.setScalar(1 + 0.03 * wave(t));
      halo.scale.setScalar(1 + 0.05 * wave(t, 1, 0.25));
    },
  };
};

// --------------------------------------------------------------- boot
const root = new THREE.Group();
scene.add(root);
let def = null;
let at = null;

function place(t) {
  const a = TAU * t / L;
  const [x, y, z] = def.cam;
  const pull = STILL ? 0.97 : 1;     // a still is framed a little closer
  const p = new THREE.Vector3(x + 0.45 * Math.sin(a), y + 0.14 * Math.sin(2 * a), z + 0.25 * Math.cos(a));
  const tgt = at.clone();
  if (STILL) tgt.x += def.stillShift ?? 0.75;
  // A loop sits under slides whose words fill the left: pull back a little
  // and shift the lens so the subject lands in the right part of the frame.
  camera.position.copy(tgt).add(p.sub(at).multiplyScalar(STILL ? pull : 1.12));
  camera.lookAt(tgt);
  if (!STILL) camera.setViewOffset(W, H, -Math.round(0.07 * W), 0, W, H);
}

function render(t) {
  def.update(t);
  // The sting holds the camera at the loop's opening pose, pushed back a little.
  if (t < 0) {
    place(0);
    const u = (t + STING) / STING;
    camera.position.z += 1.6 * (1 - easeOut(u));
  } else {
    place(t);
  }
  composer.render();
  return true;
}

async function boot() {
  // Text is drawn into textures, so the fonts must be in before any scene is built.
  await Promise.all([document.fonts.load('600 100px IBM Plex Sans'), document.fonts.load('500 40px IBM Plex Sans')]);
  await document.fonts.ready;
  def = SCENES[NAME](root);
  at = new THREE.Vector3(...def.at);
  bench(def.at[0] + 2.2);
  render(0);
  return true;
}

window.kit = { L, STING, scene: NAME, ready: boot(), render };
