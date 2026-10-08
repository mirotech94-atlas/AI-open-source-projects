// Gesture maths for Air Glass.
//
// Pure functions and small state machines: no DOM, no three.js, no network.
// Node imports this file for the tests (apps/airglass/test/), the browser
// imports it from main.js. Every position here is in SCREEN PIXELS of the
// viewport, y down, after the mirror and the object-fit: cover crop have been
// applied by videoToScreen(). Time is in seconds.

// The 21 MediaPipe hand landmarks we use, by index.
export const LM = Object.freeze({
  WRIST: 0,
  THUMB_IP: 3, THUMB_TIP: 4,
  INDEX_MCP: 5, INDEX_PIP: 6, INDEX_TIP: 8,
  MIDDLE_MCP: 9, MIDDLE_PIP: 10, MIDDLE_TIP: 12,
  RING_MCP: 13, RING_PIP: 14, RING_TIP: 16,
  PINKY_MCP: 17, PINKY_PIP: 18, PINKY_TIP: 20,
});

export const TUNING = Object.freeze({
  // Pinch: thumb tip to index tip, divided by hand size (wrist to middle MCP),
  // so the threshold holds whether the hand is near the camera or far from it.
  pinchOn: 0.32,       // ratio below which a pinch starts
  pinchOff: 0.48,      // ratio above which it ends (hysteresis band in between)
  // Toss: release speed, in viewport widths per second.
  tossSpeed: 0.9,
  velocityWindow: 0.12, // seconds of history used for the release velocity
  palmHoldS: 1.0,       // open palm held this long toggles the menu
  damping: 1.6,         // 1/s, exponential velocity decay while in flight
  stopSpeed: 0.05,      // world units/s below which a flight ends
});

// ---------------------------------------------------------------- geometry

export const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
export const mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });

/** Wrap an angle to (-PI, PI]. */
export function wrapAngle(a) {
  let x = a % (2 * Math.PI);
  if (x <= -Math.PI) x += 2 * Math.PI;
  if (x > Math.PI) x -= 2 * Math.PI;
  return x;
}

/**
 * Map one MediaPipe landmark (normalised to the VIDEO frame, x right, y down,
 * z relative depth on the x scale) to viewport pixels, given that the video is
 * shown with object-fit: cover and, for a selfie view, mirrored.
 */
export function videoToScreen(p, video, view, mirror = true) {
  const s = Math.max(view.w / video.w, view.h / video.h);
  const ox = (view.w - video.w * s) / 2;
  const oy = (view.h - video.h * s) / 2;
  const nx = mirror ? 1 - p.x : p.x;
  return {
    x: nx * video.w * s + ox,
    y: p.y * video.h * s + oy,
    z: (p.z ?? 0) * video.w * s,
  };
}

/** Wrist to middle-finger MCP, in pixels: the hand's scale. */
export const handSize = (lm) => Math.max(1e-6, dist(lm[LM.WRIST], lm[LM.MIDDLE_MCP]));

/** Thumb-tip to index-tip distance over hand size. */
export const pinchRatio = (lm) => dist(lm[LM.THUMB_TIP], lm[LM.INDEX_TIP]) / handSize(lm);

/** The point a pinch holds: halfway between thumb tip and index tip. */
export const pinchPoint = (lm) => mid(lm[LM.THUMB_TIP], lm[LM.INDEX_TIP]);

function extended(lm, tip, pip) {
  const w = lm[LM.WRIST];
  return dist(w, lm[tip]) > dist(w, lm[pip]) * 1.12;
}

/** All four fingers straight and the thumb away from the index: an open palm. */
export function isOpenPalm(lm) {
  return extended(lm, LM.INDEX_TIP, LM.INDEX_PIP)
    && extended(lm, LM.MIDDLE_TIP, LM.MIDDLE_PIP)
    && extended(lm, LM.RING_TIP, LM.RING_PIP)
    && extended(lm, LM.PINKY_TIP, LM.PINKY_PIP)
    && pinchRatio(lm) > 0.6;
}

/** Index straight, middle/ring/pinky curled: pointing. */
export function isPointing(lm) {
  return extended(lm, LM.INDEX_TIP, LM.INDEX_PIP)
    && !extended(lm, LM.MIDDLE_TIP, LM.MIDDLE_PIP)
    && !extended(lm, LM.RING_TIP, LM.RING_PIP)
    && !extended(lm, LM.PINKY_TIP, LM.PINKY_PIP);
}

const sub3 = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross3 = (a, b) => [
  a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
function norm3(a) {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
}

/**
 * An orthonormal frame for the palm, in a right-handed space with x right,
 * y UP and z toward the viewer (so pixel y and MediaPipe z are negated).
 * Returns {x, y, z} column vectors. The object's rotation follows the change
 * in this frame between the start of a grab and now.
 */
export function palmBasis(lm) {
  const v = (p) => [p.x, -p.y, -(p.z ?? 0)];
  const wrist = v(lm[LM.WRIST]);
  const up = norm3(sub3(v(lm[LM.MIDDLE_MCP]), wrist));
  const across = sub3(v(lm[LM.INDEX_MCP]), v(lm[LM.PINKY_MCP]));
  const n = norm3(cross3(across, up));
  const right = norm3(cross3(up, n));
  return { x: right, y: up, z: n };
}

// ---------------------------------------------------------------- filters

/** One-euro filter (Casiez et al. 2012): smooth at rest, responsive in motion. */
export class OneEuro {
  constructor(minCutoff = 1.2, beta = 0.02, dCutoff = 1.0) {
    Object.assign(this, { minCutoff, beta, dCutoff });
    this.reset();
  }
  reset() { this.x = null; this.dx = 0; this.t = null; }
  static alpha(cutoff, dt) {
    const tau = 1 / (2 * Math.PI * cutoff);
    return 1 / (1 + tau / dt);
  }
  filter(x, t) {
    if (this.t === null) { this.x = x; this.t = t; this.dx = 0; return x; }
    const dt = Math.max(t - this.t, 1e-3);
    this.t = t;
    const ad = OneEuro.alpha(this.dCutoff, dt);
    this.dx = ad * ((x - this.x) / dt) + (1 - ad) * this.dx;
    const a = OneEuro.alpha(this.minCutoff + this.beta * Math.abs(this.dx), dt);
    this.x = a * x + (1 - a) * this.x;
    return this.x;
  }
}

/** Pinch with hysteresis: starts under pinchOn, ends only above pinchOff. */
export class Pinch {
  constructor(on = TUNING.pinchOn, off = TUNING.pinchOff) {
    this.on = on; this.off = off; this.active = false;
  }
  update(ratio) {
    const was = this.active;
    if (!was && ratio < this.on) this.active = true;
    else if (was && ratio > this.off) this.active = false;
    return { active: this.active, started: !was && this.active, ended: was && !this.active };
  }
}

/** Release velocity from the last `window` seconds of samples (px/s). */
export class Velocity {
  constructor(window = TUNING.velocityWindow) { this.window = window; this.s = []; }
  reset() { this.s = []; }
  push(t, x, y) {
    this.s.push({ t, x, y });
    while (this.s.length > 2 && t - this.s[0].t > this.window) this.s.shift();
  }
  get() {
    if (this.s.length < 2) return { vx: 0, vy: 0, speed: 0 };
    const a = this.s[0], b = this.s[this.s.length - 1];
    const dt = b.t - a.t;
    if (dt < 1e-3) return { vx: 0, vy: 0, speed: 0 };
    const vx = (b.x - a.x) / dt, vy = (b.y - a.y) / dt;
    return { vx, vy, speed: Math.hypot(vx, vy) };
  }
}

/** A release is a toss when it leaves faster than tossSpeed viewport widths/s. */
export const isToss = (speedPx, viewportW, tossSpeed = TUNING.tossSpeed) =>
  speedPx > tossSpeed * viewportW;

/**
 * Two-hand scale and rotation. start() records the distance and angle between
 * the two pinch points; update() returns the scale ratio and the angle change
 * since start, unwrapped so crossing +/-180 degrees never jumps by a turn.
 * The angle is measured in screen pixels (y down): positive is clockwise.
 */
export class TwoHand {
  start(a, b) {
    this.d0 = Math.max(1e-6, dist(a, b));
    this.last = Math.atan2(b.y - a.y, b.x - a.x);
    this.turn = 0;
    this.mid0 = mid(a, b);
  }
  update(a, b) {
    const ang = Math.atan2(b.y - a.y, b.x - a.x);
    this.turn += wrapAngle(ang - this.last);
    this.last = ang;
    return { scale: dist(a, b) / this.d0, angle: this.turn, mid: mid(a, b), mid0: this.mid0 };
  }
}

/** Fires once when `cond` has held for `holdS`; re-arms when cond drops. */
export class Hold {
  constructor(holdS = TUNING.palmHoldS) { this.holdS = holdS; this.since = null; this.fired = false; }
  update(cond, t) {
    if (!cond) { this.since = null; this.fired = false; return { progress: 0, fire: false }; }
    if (this.since === null) this.since = t;
    const progress = Math.min(1, (t - this.since) / this.holdS);
    const fire = progress >= 1 && !this.fired;
    if (fire) this.fired = true;
    return { progress, fire };
  }
}

/** Advance a free-flying body (no gravity): x += v dt, v decays exponentially. */
export function stepFlight(body, dt, damping = TUNING.damping) {
  const k = Math.exp(-damping * dt);
  return {
    x: body.x + body.vx * dt, y: body.y + body.vy * dt,
    vx: body.vx * k, vy: body.vy * k,
  };
}

// ------------------------------------------------------------ hand tracker

/**
 * Keeps up to two hands' identities stable from frame to frame (nearest
 * wrist), smooths every landmark with a one-euro filter, and runs a pinch
 * detector per hand. Input: arrays of 21 points already in screen pixels.
 */
export class HandTracker {
  constructor() { this.hands = []; this.nextId = 1; }

  update(rawHands, t) {
    const prev = this.hands;
    const used = new Set();
    const out = [];
    for (const raw of rawHands.slice(0, 2)) {
      let best = null, bestD = Infinity;
      for (const h of prev) {
        if (used.has(h.id)) continue;
        const d = dist(h.lm[LM.WRIST], raw[LM.WRIST]);
        if (d < bestD) { bestD = d; best = h; }
      }
      // A wrist that jumped more than 3 hand sizes is a new hand, not this one.
      if (best && bestD > 3 * handSize(best.lm)) best = null;
      const h = best ?? {
        id: this.nextId++, pinch: new Pinch(), vel: new Velocity(),
        f: raw.map(() => [new OneEuro(), new OneEuro(), new OneEuro(1.0, 0.005)]),
      };
      used.add(h.id);
      h.lm = raw.map((p, i) => ({
        x: h.f[i][0].filter(p.x, t), y: h.f[i][1].filter(p.y, t),
        z: h.f[i][2].filter(p.z ?? 0, t),
      }));
      h.ratio = pinchRatio(h.lm);
      h.pinchState = h.pinch.update(h.ratio);
      h.point = pinchPoint(h.lm);
      h.vel.push(t, h.point.x, h.point.y);
      out.push(h);
    }
    const lost = prev.filter((h) => !used.has(h.id));
    this.hands = out;
    return { hands: out, lost };
  }
}

// ---------------------------------------------------------- gesture engine

/**
 * Turns tracked hands into intents. Pure: the caller supplies the viewport
 * width and an `overMenu(x, y)` hit test, and applies what comes out.
 *
 * modes: IDLE, GRAB (one hand pinching), TWO-HAND, FLIGHT is the caller's.
 * events (in order):
 *   {type:'select', x, y}                 pinch began over an open menu
 *   {type:'grab-start', hand}             one-hand grab begins (re-anchors)
 *   {type:'two-start', a, b}              two-hand grab begins
 *   {type:'release', toss, vx, vy}        last pinch let go (px/s velocity)
 *   {type:'palm'}                         open palm held palmHoldS
 */
export class GestureEngine {
  constructor(opts = {}) {
    this.viewportW = opts.viewportW ?? 1280;
    this.tossSpeed = opts.tossSpeed ?? TUNING.tossSpeed;
    this.mode = 'IDLE';
    this.grabbers = [];      // hand ids currently holding the object, in order
    this.consumed = new Set(); // ids whose pinch was spent on a menu select
    this.two = new TwoHand();
    this.palm = new Hold(opts.palmHoldS ?? TUNING.palmHoldS);
    this.lastHands = new Map();
  }

  update({ hands, lost = [] }, t, overMenu = () => false) {
    const events = [];
    const byId = new Map(hands.map((h) => [h.id, h]));

    // A hand that vanished mid-grab (fast flicks blur out of the tracker) is
    // a release, and keeps the velocity it had when it was last seen.
    const holding = (h) => byId.has(h) && byId.get(h).pinchState.active;
    for (const h of hands) {
      if (h.pinchState.started && overMenu(h.point.x, h.point.y)) {
        this.consumed.add(h.id);
        events.push({ type: 'select', x: h.point.x, y: h.point.y });
      }
      if (!h.pinchState.active) this.consumed.delete(h.id);
    }
    for (const h of lost) this.consumed.delete(h.id);

    const before = this.grabbers;
    const still = before.filter(holding);
    const fresh = hands
      .filter((h) => h.pinchState.active && !this.consumed.has(h.id) && !still.includes(h.id))
      .map((h) => h.id);
    const now = [...still, ...fresh].slice(0, 2);

    if (now.length === 0 && before.length > 0) {
      const id = before[before.length - 1];
      const h = byId.get(id) ?? lost.find((x) => x.id === id) ?? this.lastHands.get(id);
      const v = h ? h.vel.get() : { vx: 0, vy: 0, speed: 0 };
      events.push({
        type: 'release', vx: v.vx, vy: v.vy, speed: v.speed,
        toss: isToss(v.speed, this.viewportW, this.tossSpeed),
      });
    } else if (now.length === 1 && (before.length !== 1 || before[0] !== now[0])) {
      events.push({ type: 'grab-start', hand: byId.get(now[0]) });
    } else if (now.length === 2 && (before.length !== 2
        || before[0] !== now[0] || before[1] !== now[1])) {
      const [a, b] = now.map((id) => byId.get(id));
      this.two.start(a.point, b.point);
      events.push({ type: 'two-start', a, b });
    }
    this.grabbers = now;
    this.mode = now.length === 2 ? 'TWO-HAND' : now.length === 1 ? 'GRAB' : 'IDLE';

    let twoHand = null;
    if (now.length === 2) {
      const [a, b] = now.map((id) => byId.get(id));
      twoHand = this.two.update(a.point, b.point);
    }

    // Open palm toggles the menu; only when nothing is held.
    const palmHand = now.length === 0 ? hands.find((h) => isOpenPalm(h.lm)) : undefined;
    const palm = this.palm.update(Boolean(palmHand), t);
    if (palm.fire) events.push({ type: 'palm' });

    this.lastHands = new Map(hands.map((h) => [h.id, h]));
    return {
      mode: this.mode, events, twoHand, palmProgress: palm.progress,
      grabHand: now.length === 1 ? byId.get(now[0]) : null,
    };
  }
}
