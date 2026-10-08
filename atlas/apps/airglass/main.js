// Air Glass: wires camera (or demo hand) -> gestures -> scene + HUD + voice.
//
// Privacy: every frame is processed in this tab. The only network requests
// are the pinned library and model files below (and, only if switched on,
// one GET of ATLAS's public /healthz a minute). The page's CSP enforces it.
import { Hud } from './hud.js';
import { Voice } from './voice.js';
import {
  HandTracker, GestureEngine, videoToScreen, palmBasis, stepFlight, isPointing, LM, TUNING,
} from './gestures.js';
import { demoFrame, DEMO_LENGTH } from './demo.js';
import { classifyHealth, readHealth, POLL_MS } from './health.js';

const MP_VERSION = '1.1.0';
const MP_BUNDLE = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${MP_VERSION}/vision_bundle.mjs`;
const MP_WASM = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${MP_VERSION}/wasm`;
// Versioned path ("float16/1"): the published HandLandmarker model, pinned.
const HAND_MODEL = 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task';
const CAMERA_MSG = 'Camera unavailable — allow camera access or use the demo mode';

// Read-only state for anyone inspecting the page (and the verification run).
const state = window.__airglass = {
  source: 'starting', camera: 'starting', gesture: 'IDLE', hands: 0, menuOpen: false,
  current: null, grabbed: false, flying: false, caught: [], selected: [], events: [],
  hologram: true, voice: true, errors: [],
};
const log = (type, extra = {}) => {
  state.events.push({ type, t: +performance.now().toFixed(0), ...extra });
  if (state.events.length > 200) state.events.shift();
};

// ------------------------------------------------------------------ setup

// three.js comes from the pinned CDN URL; if it cannot load, say so instead
// of leaving a blank page (scene.js is the only module that needs it).
let Stage, PROPS = [], loadModelFiles, cloneProp, THREE;
let sceneError = null;
try {
  ({ Stage, PROPS, loadModelFiles, cloneProp, THREE } = await import('./scene.js'));
} catch (e) {
  sceneError = e;
  state.errors.push(`three.js: ${e?.message ?? e}`);
}

const video = document.querySelector('#cam');
let stage;
try {
  if (Stage) stage = new Stage(document.querySelector('#gl'));
} catch (e) {
  state.errors.push(`webgl: ${e.message}`);
}
const voice = new Voice();
const tracker = new HandTracker();
const engine = new GestureEngine({ viewportW: window.innerWidth });

const custom = [];            // models opened from disk this session
const vault = [];             // caught props: {name, line, key, object, at}
let currentEntry = null;      // {key, name, line, object}

const hud = new Hud({
  onPick: (key) => pick(key),
  onFiles: (files) => openFiles(files),
  onToggle: (name) => toggle(name),
  onRetryCamera: () => startCamera(),
});
hud.setProps(PROPS);
hud.setVault(vault);

// Chrome fills the voice list after load, and refuses speech until the page
// has had a click or a key press: keep the toggle label and a one-time
// "click once" hint in step with both.
function refreshVoiceUi() {
  hud.setToggle('voice', state.voice,
    state.voice ? (voice.available ? 'ON' : 'CAPTIONS') : 'OFF');
  const blocked = navigator.userActivation ? !navigator.userActivation.hasBeenActive : false;
  hud.speakHint(state.voice && voice.available && blocked);
}
voice.onChange = refreshVoiceUi;
refreshVoiceUi();
for (const ev of ['pointerdown', 'keydown', 'click']) {
  window.addEventListener(ev, () => setTimeout(refreshVoiceUi, 0), { capture: true });
}
Object.defineProperty(state, 'speech', {
  enumerable: true,
  get: () => ({ voice: voice.voice?.name ?? null, started: voice.started,
    lastError: voice.lastError }),
});
if (sceneError) {
  hud.showMessage('3D library could not load',
    'three.js comes from a pinned jsDelivr URL and did not download. Check the connection '
    + '(and DNS for cdn.jsdelivr.net), then reload.');
} else if (!stage) {
  hud.showMessage('WebGL unavailable',
    'This browser could not create a WebGL context, so the hologram cannot be drawn.');
}

function entryFor(key) {
  const [kind, id] = key.split(':');
  if (kind === 'prop') {
    const p = PROPS.find((x) => x.id === id);
    if (p) return { key, name: p.name, line: p.line, object: p.make() };
    const c = custom.find((x) => x.id === id);
    if (c) return { key, name: c.name, line: 'Caught it.', object: cloneProp(c.object) };
  }
  return null;
}

/** Where a newly summoned prop appears: between the hands if two are up. */
function spawnPoint() {
  const hs = tracker.hands;
  if (hs.length === 2) {
    const a = hs[0].lm[LM.MIDDLE_MCP], b = hs[1].lm[LM.MIDDLE_MCP];
    return stage.screenToWorld((a.x + b.x) / 2, (a.y + b.y) / 2);
  }
  return new THREE.Vector3(0, 0, 0);
}

function show(entry) {
  if (!stage || !entry) return;
  flight = null;
  stage.show(entry.object, spawnPoint());
  currentEntry = entry;
  state.current = entry.name;
  hud.setActive(entry.key);
  log('show', { name: entry.name });
}

function pick(key) {
  if (key.startsWith('vault:')) {
    const [it] = vault.splice(Number(key.split(':')[1]), 1);
    if (!it) return;
    hud.setVault(vault);
    show(it);
    hud.caption(`${it.name}, back from the vault.`);
  } else {
    show(entryFor(key));
  }
  state.selected.push(key);
  log('select', { key });
  if (state.source === 'demo' || hud.menuOpen) setTimeout(() => setMenu(false), 900);
}

async function openFiles(files) {
  try {
    hud.caption('Loading model from disk…', 'FILE');
    const { name, object } = await loadModelFiles(files);
    const id = `file${custom.length + 1}`;
    custom.push({ id, name, object });
    hud.setProps(PROPS, custom);
    show({ key: `prop:${id}`, name, line: 'Caught it.', object: cloneProp(object) });
    hud.caption(`${name} loaded. It never left this Mac.`, 'FILE');
  } catch (e) {
    hud.caption(`Could not load that file: ${e.message}`, 'FILE');
    state.errors.push(`file: ${e.message}`);
  }
}

function setMenu(open) {
  hud.setMenu(open);
  state.menuOpen = open;
}

// ---------------------------------------------------------------- toggles

let healthTimer = null;
async function pollHealth() {
  const at = new Date();
  const verdict = classifyHealth(await readHealth());
  if (healthTimer !== null) hud.orb(verdict.state, verdict.detail, at);
  state.health = { ...verdict, at: at.toISOString() };
}

function toggle(name) {
  if (name === 'hologram') {
    state.hologram = !state.hologram;
    stage?.setHologram(state.hologram);
    hud.setToggle('hologram', state.hologram, state.hologram ? 'HOLO' : 'SOLID');
  } else if (name === 'voice') {
    voice.enabled = state.voice = !state.voice;
    refreshVoiceUi();
  } else if (name === 'status') {
    if (healthTimer === null) {
      healthTimer = setInterval(pollHealth, POLL_MS);
      hud.orb('UNKNOWN', 'reading…');
      hud.setToggle('status', true, 'ON');
      pollHealth();
    } else {
      clearInterval(healthTimer);
      healthTimer = null;
      hud.orb('OFF');
      hud.setToggle('status', false, 'OFF');
    }
  } else if (name === 'demo') {
    if (state.source === 'demo') stopDemo(); else startDemo();
  } else if (name === 'menu') {
    setMenu(!hud.menuOpen);
  }
}

window.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const k = { h: 'hologram', v: 'voice', d: 'demo', m: 'menu', s: 'status' }[e.key.toLowerCase()];
  if (k) toggle(k);
});

// ----------------------------------------------------------------- camera

let landmarker = null;
let stream = null;
let lastVideoTime = -1;

async function startCamera() {
  hud.hideMessage();
  state.camera = 'requesting';
  state.source = 'camera';
  hud.setToggle('demo', false, 'OFF');
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('no mediaDevices');
    stream?.getTracks().forEach((t) => t.stop());
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();
    document.body.classList.add('has-video');
    state.camera = 'ok';
    log('camera', { w: video.videoWidth, h: video.videoHeight });
  } catch (e) {
    // NotAllowed, NotFound, NotReadable, Overconstrained, no mediaDevices: all
    // end here, and all say the same plain thing.
    state.camera = 'unavailable';
    state.source = 'none';
    state.cameraError = e?.name || String(e);
    log('camera-unavailable', { reason: state.cameraError });
    hud.showMessage(CAMERA_MSG,
      'Air Glass needs a webcam on http://127.0.0.1 (or https). Nothing is recorded or sent.');
    return;
  }
  if (!landmarker) await loadLandmarker();
}

async function loadLandmarker() {
  state.camera = 'loading-model';
  hud.caption('Loading the hand model (pinned, from Google’s model store)…', 'LOCAL');
  try {
    const { FilesetResolver, HandLandmarker } = await import(MP_BUNDLE);
    const fileset = await FilesetResolver.forVisionTasks(MP_WASM);
    const make = (delegate) => HandLandmarker.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: HAND_MODEL, delegate },
      runningMode: 'VIDEO', numHands: 2,
      minHandDetectionConfidence: 0.6, minHandPresenceConfidence: 0.5,
      minTrackingConfidence: 0.5,
    });
    try {
      landmarker = await make('GPU');
      state.delegate = 'GPU';
    } catch {
      landmarker = await make('CPU');
      state.delegate = 'CPU';
    }
    state.camera = 'tracking';
    log('model-ready', { delegate: state.delegate });
    hud.caption('Hand tracking is live. Pinch to grab.', 'LOCAL');
  } catch (e) {
    state.camera = 'model-failed';
    state.errors.push(`model: ${e?.message ?? e}`);
    hud.showMessage('Hand model could not load',
      'The pinned MediaPipe files did not download (offline?). Demo mode still works.');
  }
}

/** Raw hands from the camera, in viewport px, or null when no new frame. */
function cameraHands() {
  if (!landmarker || video.readyState < 2 || video.currentTime === lastVideoTime) return null;
  lastVideoTime = video.currentTime;
  const res = landmarker.detectForVideo(video, performance.now());
  const vid = { w: video.videoWidth, h: video.videoHeight };
  const view = { w: window.innerWidth, h: window.innerHeight };
  return (res.landmarks ?? []).map((hand) => hand.map((p) => videoToScreen(p, vid, view, true)));
}

// ------------------------------------------------------------------- demo

let demoStart = 0;
let lastDemoLoop = -1;

function startDemo() {
  hud.hideMessage();
  state.source = 'demo';
  if (!landmarker) state.camera = 'off';
  demoStart = performance.now() / 1000;
  lastDemoLoop = -1;
  tracker.hands = [];
  hud.setToggle('demo', true, 'ON');
  document.body.classList.add('demo');
  log('demo-start');
}

function stopDemo() {
  state.source = landmarker ? 'camera' : 'none';
  hud.setToggle('demo', false, 'OFF');
  hud.demoCaption(null);
  document.body.classList.remove('demo');
  tracker.hands = [];
  if (!landmarker) startCamera();
}

function demoHands(now) {
  const t = now - demoStart;
  const loop = Math.floor(t / DEMO_LENGTH);
  if (loop !== lastDemoLoop) {            // each loop starts with a prop in view
    lastDemoLoop = loop;
    if (!stage?.current) show(entryFor(`prop:${PROPS[loop % PROPS.length].id}`));
    stage?.holder.position.set(0, 0, 0);
  }
  const view = { w: window.innerWidth, h: window.innerHeight };
  const f = demoFrame(t, view, { propPoint: hud.pointOf('prop:knot') });
  hud.demoCaption(f.caption);
  return f.hands;
}

// ---------------------------------------------------------------- physics

let grab = null;      // anchors captured when a grab starts
let flight = null;    // {vx, vy, spin} in world units/s while tossed

const quatFromBasis = (b) => new THREE.Quaternion().setFromRotationMatrix(
  new THREE.Matrix4().makeBasis(new THREE.Vector3(...b.x), new THREE.Vector3(...b.y),
    new THREE.Vector3(...b.z)));

function startGrab(hand) {
  flight = null;
  const h = stage.holder;
  grab = {
    kind: 'one', point: { ...hand.point }, pos: h.position.clone(), q: h.quaternion.clone(),
    handQ: quatFromBasis(palmBasis(hand.lm)), scale: h.scale.x,
  };
}

function startTwo() {
  flight = null;
  const h = stage.holder;
  grab = { kind: 'two', pos: h.position.clone(), q: h.quaternion.clone(), scale: h.scale.x };
}

function applyGestures(out) {
  const h = stage.holder;
  const k = stage.unitsPerPx;
  if (out.mode === 'GRAB' && grab?.kind === 'one' && out.grabHand) {
    const hand = out.grabHand;
    h.position.set(grab.pos.x + (hand.point.x - grab.point.x) * k,
      grab.pos.y - (hand.point.y - grab.point.y) * k, grab.pos.z);
    const delta = quatFromBasis(palmBasis(hand.lm)).multiply(grab.handQ.clone().invert());
    h.quaternion.slerp(delta.multiply(grab.q), 0.5);
  } else if (out.mode === 'TWO-HAND' && grab?.kind === 'two' && out.twoHand) {
    const t = out.twoHand;
    h.scale.setScalar(THREE.MathUtils.clamp(grab.scale * t.scale, 0.25, 4));
    // Screen angles are clockwise-positive (y down); world z is counter-clockwise.
    const turn = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), -t.angle);
    h.quaternion.copy(turn.multiply(grab.q));
    h.position.set(grab.pos.x + (t.mid.x - t.mid0.x) * k, grab.pos.y - (t.mid.y - t.mid0.y) * k,
      grab.pos.z);
  }
}

function caught() {
  const entry = currentEntry;
  flight = null;
  stage.clear();
  currentEntry = null;
  state.current = null;
  hud.setActive(null);
  if (!entry) return;
  const at = new Date();
  vault.unshift({ ...entry, key: entry.key, at });
  hud.setVault(vault);
  hud.catchFlash();
  hud.caption(entry.line);
  const spoken = voice.say(entry.line);
  state.caught.push(entry.name);
  log('caught', { name: entry.name, spoken });
}

function stepPhysics(dt) {
  if (!flight || !stage.current) return;
  const h = stage.holder;
  const b = stepFlight({ x: h.position.x, y: h.position.y, vx: flight.vx, vy: flight.vy }, dt);
  h.position.x = b.x; h.position.y = b.y;
  flight.vx = b.vx; flight.vy = b.vy;
  h.rotateY(flight.spin * dt);
  flight.spin *= Math.exp(-TUNING.damping * dt);
  const s = stage.worldToScreen(h.position);
  if (s.x >= hud.catchLineX()) { caught(); return; }
  // Other edges: bounce back softly, so nothing is lost off-screen.
  const halfW = stage.view.w / 2 * stage.unitsPerPx, halfH = stage.view.h / 2 * stage.unitsPerPx;
  if (h.position.x < -halfW) { h.position.x = -halfW; flight.vx = Math.abs(flight.vx) * 0.5; }
  if (Math.abs(h.position.y) > halfH) {
    h.position.y = Math.sign(h.position.y) * halfH; flight.vy = -flight.vy * 0.5;
  }
  if (Math.hypot(flight.vx, flight.vy) < TUNING.stopSpeed) flight = null;
}

function handleEvents(out) {
  for (const e of out.events) {
    if (e.type === 'select') {
      const el = hud.itemAt(e.x, e.y);
      log('pinch-menu', { item: el?.dataset.pick ?? null });
      if (el) pick(el.dataset.pick);
    } else if (e.type === 'palm') {
      setMenu(!hud.menuOpen);
      log('palm', { open: hud.menuOpen });
    } else if (!stage?.current) {
      continue;
    } else if (e.type === 'grab-start') {
      startGrab(e.hand);
      log('grab');
    } else if (e.type === 'two-start') {
      startTwo();
      log('two-hand');
    } else if (e.type === 'release') {
      grab = null;
      if (e.toss) {
        const k = stage.unitsPerPx;
        flight = { vx: e.vx * k, vy: -e.vy * k, spin: Math.sign(e.vx || 1) * 6 };
        log('toss', { vx: Math.round(e.vx), vy: Math.round(e.vy) });
      } else {
        log('release');
      }
    }
  }
}

// ------------------------------------------------------------------- loop

let fps = 0;
let lastFrame = performance.now();
let lastOut = { mode: 'IDLE', events: [], palmProgress: 0 };

function frame() {
  requestAnimationFrame(frame);
  const nowMs = performance.now();
  const dtMs = nowMs - lastFrame;
  lastFrame = nowMs;
  fps = fps ? fps * 0.92 + (1000 / Math.max(dtMs, 1)) * 0.08 : 60;
  const now = nowMs / 1000;

  let raw = null;
  try {
    raw = state.source === 'demo' ? demoHands(now) : cameraHands();
  } catch (e) {
    state.errors.push(`frame: ${e.message}`);
  }
  if (raw) {
    const tracked = tracker.update(raw, now);
    engine.viewportW = window.innerWidth;
    lastOut = engine.update(tracked, now, (x, y) => hud.overMenu(x, y));
    if (stage) {
      handleEvents(lastOut);
      if (stage.current) applyGestures(lastOut);
    }
    if (hud.menuOpen) {
      // The cursor is the pinch reticle (between thumb and index tip): what it
      // hovers is what a pinch selects.
      hud.hover(tracked.hands.filter((h) => isPointing(h.lm) || h.ratio < 0.9)
        .map((h) => h.point));
    }
    state.hands = tracked.hands.length;
  }

  const mode = flight ? 'TOSS' : lastOut.mode;
  state.gesture = mode;
  state.grabbed = lastOut.mode !== 'IDLE';
  state.flying = Boolean(flight);
  hud.drawHands(tracker.hands, { palmProgress: lastOut.palmProgress, mode: lastOut.mode });
  hud.status({
    hands: tracker.hands.length, fps, source: state.source.toUpperCase(),
    gesture: hud.menuOpen && mode === 'IDLE' ? 'MENU' : mode,
  });

  if (!stage) return;
  const dt = Math.min(dtMs / 1000, 0.1);
  stepPhysics(dt);
  stage.render(lastOut.mode === 'IDLE' && !flight);
  const h = stage.holder;
  const e = new THREE.Euler().setFromQuaternion(h.quaternion, 'XYZ');
  hud.kinetics({
    pos: h.position, scale: h.scale.x,
    rot: [e.x, e.y, e.z].map(THREE.MathUtils.radToDeg),
    speed: flight ? Math.hypot(flight.vx, flight.vy) : 0,
  });
}

window.addEventListener('resize', () => {
  stage?.resize();
  hud.resize();
});

// ------------------------------------------------------------------ start

show(entryFor('prop:globe'));
requestAnimationFrame(frame);
const params = new URLSearchParams(location.search);
if (sceneError) {
  // the message above stays up; no camera request without something to show
} else if (params.has('demo')) {
  if (params.get('menu') === 'open') setMenu(true);
  startDemo();
} else {
  startCamera();
}
