// Run: node --test apps/airglass/test/
// Node's built-in runner only; nothing to install.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  Pinch, TwoHand, Velocity, OneEuro, Hold, HandTracker, GestureEngine,
  isToss, isOpenPalm, isPointing, pinchRatio, palmBasis, stepFlight, videoToScreen,
  wrapAngle, TUNING,
} from '../gestures.js';
import { synthHand, demoFrame, DEMO_LENGTH } from '../demo.js';
import { classifyHealth, readHealth, HEARTBEAT_MAX_S } from '../health.js';

const deg = (d) => (d * Math.PI) / 180;
const close = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

// ---------------------------------------------------------------- pinch

test('pinch: hysteresis between pinchOn and pinchOff', () => {
  const p = new Pinch(0.32, 0.48);
  const seq = [0.9, 0.4, 0.31, 0.4, 0.47, 0.49, 0.4, 0.33, 0.2];
  const active = seq.map((r) => p.update(r).active);
  assert.deepEqual(active, [false, false, true, true, true, false, false, false, true]);
});

test('pinch: started/ended fire once per transition', () => {
  const p = new Pinch();
  assert.equal(p.update(0.1).started, true);
  assert.equal(p.update(0.1).started, false);
  assert.equal(p.update(0.9).ended, true);
  assert.equal(p.update(0.9).ended, false);
});

test('pinch ratio is independent of hand size (near or far from the camera)', () => {
  for (const pinch of [0, 0.5, 1]) {
    const near = pinchRatio(synthHand({ x: 300, y: 400, size: 220, pinch }));
    const far = pinchRatio(synthHand({ x: 300, y: 400, size: 45, pinch }));
    close(near, far, 1e-9);
  }
  assert.ok(pinchRatio(synthHand({ x: 0, y: 0, size: 100, pinch: 0 })) > TUNING.pinchOff);
  assert.ok(pinchRatio(synthHand({ x: 0, y: 0, size: 100, pinch: 1 })) < TUNING.pinchOn);
});

test('open palm vs pointing', () => {
  const open = synthHand({ x: 0, y: 0, size: 100 });
  const point = synthHand({ x: 0, y: 0, size: 100, point: true });
  assert.equal(isOpenPalm(open), true);
  assert.equal(isPointing(open), false);
  assert.equal(isOpenPalm(point), false);
  assert.equal(isPointing(point), true);
  assert.equal(isOpenPalm(synthHand({ x: 0, y: 0, size: 100, pinch: 1 })), false);
});

// ---------------------------------------------------------------- two hands

test('two-hand: scale is the ratio of distances', () => {
  const th = new TwoHand();
  th.start({ x: 0, y: 0 }, { x: 100, y: 0 });
  close(th.update({ x: 0, y: 0 }, { x: 200, y: 0 }).scale, 2);
  close(th.update({ x: 50, y: 0 }, { x: 100, y: 0 }).scale, 0.5);
});

test('two-hand: rotation is the change in angle (screen, clockwise positive)', () => {
  const th = new TwoHand();
  th.start({ x: 0, y: 0 }, { x: 100, y: 0 });
  close(th.update({ x: 0, y: 0 }, { x: 0, y: 100 }).angle, deg(90));
  close(th.update({ x: 0, y: 0 }, { x: 100, y: 0 }).angle, 0);
});

test('two-hand: crossing +/-180 degrees does not jump by a turn', () => {
  const th = new TwoHand();
  const at = (d) => ({ x: 100 * Math.cos(deg(d)), y: 100 * Math.sin(deg(d)) });
  th.start({ x: 0, y: 0 }, at(179));
  close(th.update({ x: 0, y: 0 }, at(-179)).angle, deg(2), 1e-9);
  close(th.update({ x: 0, y: 0 }, at(-90)).angle, deg(91), 1e-9);
  // and all the way round: a full turn accumulates to 2*PI, not 0
  for (const d of [0, 90, 179]) th.update({ x: 0, y: 0 }, at(d));
  close(th.update({ x: 0, y: 0 }, at(-179)).angle, deg(362), 1e-9);
  close(wrapAngle(deg(181)), deg(-179), 1e-9);
});

// ---------------------------------------------------------------- toss

test('velocity over the recent window, and the toss threshold', () => {
  const v = new Velocity(0.12);
  for (let i = 0; i <= 20; i++) v.push(i / 100, 2000 * (i / 100), 0); // 2000 px/s
  const r = v.get();
  close(r.vx, 2000, 1e-6);
  close(r.vy, 0);
  assert.equal(isToss(r.speed, 1280), true);             // 1.56 widths/s
  assert.equal(isToss(300, 1280), false);                 // 0.23 widths/s
  assert.equal(isToss(0.9 * 1280 + 1, 1280), true);
});

test('velocity window forgets old motion (a pause before letting go)', () => {
  const v = new Velocity(0.12);
  for (let i = 0; i <= 10; i++) v.push(i / 100, 3000 * (i / 100), 0); // fast...
  for (let i = 11; i <= 40; i++) v.push(i / 100, 300, 0);             // ...then still
  assert.equal(v.get().speed, 0);
});

test('flight: damping, no gravity', () => {
  let b = { x: 0, y: 0, vx: 2, vy: 0 };
  for (let i = 0; i < 60; i++) b = stepFlight(b, 1 / 60, 1.6);
  assert.equal(b.y, 0);
  assert.equal(b.vy, 0);
  close(b.vx, 2 * Math.exp(-1.6), 1e-9);
  assert.ok(b.x > 1 && b.x < 2);
});

// ---------------------------------------------------------------- filters

test('one-euro filter reduces jitter at rest', () => {
  const f = new OneEuro();
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647 - 0.5) * 6;
  let rawVar = 0, outVar = 0;
  for (let i = 0; i < 300; i++) {
    const raw = 500 + rnd();
    const out = f.filter(raw, i / 30);
    if (i > 30) { rawVar += (raw - 500) ** 2; outVar += (out - 500) ** 2; }
  }
  assert.ok(outVar < rawVar / 3, `filtered ${outVar} vs raw ${rawVar}`);
});

test('hold fires once after the hold time, re-arms on release', () => {
  const h = new Hold(1.0);
  assert.equal(h.update(true, 0).fire, false);
  assert.equal(h.update(true, 0.99).fire, false);
  assert.equal(h.update(true, 1.0).fire, true);
  assert.equal(h.update(true, 2.0).fire, false);
  h.update(false, 2.1);
  assert.equal(h.update(true, 2.2).fire, false);
  assert.equal(h.update(true, 3.2).fire, true);
});

// ---------------------------------------------------------------- mapping

test('video to screen: object-fit cover crop and mirror', () => {
  const video = { w: 1280, h: 720 }, view = { w: 1000, h: 1000 };
  const s = 1000 / 720;                                   // cover scale
  const c = videoToScreen({ x: 0.5, y: 0.5, z: 0 }, video, view);
  close(c.x, 500, 1e-9); close(c.y, 500, 1e-9);
  const left = videoToScreen({ x: 0, y: 0, z: 0.1 }, video, view, true);
  close(left.x, 1280 * s - (1280 * s - 1000) / 2, 1e-9); // mirrored: x=0 is the right
  close(left.y, 0, 1e-9);
  close(left.z, 0.1 * 1280 * s, 1e-9);
  const unm = videoToScreen({ x: 0, y: 0 }, video, view, false);
  close(unm.x, -(1280 * s - 1000) / 2, 1e-9);
});

test('palm basis is orthonormal and follows a roll of the hand', () => {
  const a = palmBasis(synthHand({ x: 0, y: 0, size: 100, yaw: 0.3, pitch: 0.2 }));
  const dot = (u, v) => u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
  for (const [u, v] of [[a.x, a.y], [a.y, a.z], [a.x, a.z]]) close(dot(u, v), 0, 1e-9);
  for (const u of [a.x, a.y, a.z]) close(dot(u, u), 1, 1e-9);
  const up0 = palmBasis(synthHand({ x: 0, y: 0, size: 100 })).y;
  const up1 = palmBasis(synthHand({ x: 0, y: 0, size: 100, roll: deg(30) })).y;
  close(Math.atan2(up1[1], up1[0]) - Math.atan2(up0[1], up0[0]), deg(30), 1e-9);
});

// ---------------------------------------------------------------- engine

function run(frames, opts = {}) {
  const tracker = new HandTracker();
  const engine = new GestureEngine({ viewportW: 1280, ...opts });
  const events = [];
  let out;
  for (const [t, hands, overMenu] of frames) {
    out = engine.update(tracker.update(hands, t), t, overMenu);
    events.push(...out.events.map((e) => ({ ...e, t })));
  }
  return { events, out };
}

const flick = (speedPx, vanish = false) => {
  const frames = [];
  for (let i = 0; i <= 30; i++) {                         // hold still, pinched
    frames.push([i / 60, [synthHand({ x: 400, y: 400, size: 120, pinch: 1 })]]);
  }
  for (let i = 1; i <= 15; i++) {                         // move right
    const x = 400 + (speedPx * i) / 60;
    frames.push([0.5 + i / 60, [synthHand({ x, y: 400, size: 120, pinch: 1 })]]);
  }
  const x = 400 + (speedPx * 16) / 60;
  frames.push([0.5 + 16 / 60, vanish ? [] : [synthHand({ x, y: 400, size: 120, pinch: 0 })]]);
  return frames;
};

test('engine: a fast release is a toss, a slow one is not', () => {
  const fast = run(flick(2400)).events;
  assert.deepEqual(fast.map((e) => e.type), ['grab-start', 'release']);
  assert.equal(fast[1].toss, true);
  assert.ok(fast[1].vx > 1500, `vx ${fast[1].vx}`);

  const slow = run(flick(200)).events;
  assert.equal(slow.at(-1).type, 'release');
  assert.equal(slow.at(-1).toss, false);
});

test('engine: a hand that vanishes mid-flick still tosses', () => {
  const ev = run(flick(2400, true)).events;
  assert.equal(ev.at(-1).type, 'release');
  assert.equal(ev.at(-1).toss, true);
});

test('engine: two pinching hands scale and rotate, then fall back to one', () => {
  const frames = [];
  const pair = (spread, pinchB = 1) => [
    synthHand({ x: 640 - spread, y: 400, size: 100, pinch: 1 }),
    synthHand({ x: 640 + spread, y: 400, size: 100, pinch: pinchB, left: true }),
  ];
  for (let i = 0; i < 20; i++) frames.push([i / 60, pair(150)]);
  for (let i = 0; i < 60; i++) frames.push([(20 + i) / 60, pair(150 + i * 2.5)]);
  for (let i = 0; i < 40; i++) frames.push([(80 + i) / 60, pair(300)]);   // settle
  const { events, out } = run(frames);
  assert.equal(events[0].type, 'two-start');
  assert.equal(out.mode, 'TWO-HAND');
  // pinch points sit half a hand inside each wrist: 200 px apart, then 500
  close(out.twoHand.scale, 500 / 200, 0.02);
  close(out.twoHand.angle, 0, 1e-3);

  frames.push([2.1, pair(300, 0)], [2.12, pair(300, 0)]);
  const after = run(frames);
  assert.equal(after.out.mode, 'GRAB');
  assert.equal(after.events.at(-1).type, 'grab-start');
});

test('engine: a pinch that starts over the menu selects, it does not grab', () => {
  const over = () => true;
  const frames = [];
  for (let i = 0; i < 10; i++) {
    frames.push([i / 60, [synthHand({ x: 1200, y: 300, size: 100, point: true })], over]);
  }
  for (let i = 10; i < 20; i++) {
    frames.push([i / 60, [synthHand({ x: 1200, y: 300, size: 100, point: true, pinch: 1 })],
      over]);
  }
  const { events, out } = run(frames);
  assert.deepEqual(events.map((e) => e.type), ['select']);
  assert.equal(out.mode, 'IDLE');
});

test('engine: an open palm held for a second opens the menu, once', () => {
  const frames = [];
  for (let i = 0; i <= 150; i++) frames.push([i / 60, [synthHand({ x: 500, y: 500, size: 100 })]]);
  const { events } = run(frames);
  assert.deepEqual(events.map((e) => e.type), ['palm']);
  assert.ok(events[0].t >= 1.0 && events[0].t < 1.1);
});

test('demo script stays inside the viewport and loops', () => {
  const view = { w: 1440, h: 900 };
  for (let t = 0; t < DEMO_LENGTH; t += 0.25) {
    for (const h of demoFrame(t, view).hands) {
      assert.equal(h.length, 21);
      for (const p of h) assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
    }
  }
  assert.deepEqual(demoFrame(1, view).hands, demoFrame(1 + DEMO_LENGTH, view).hands);
});

// ---------------------------------------------------------------- health

test('health: fresh heartbeat is OK', () => {
  const r = classifyHealth({ status: 200,
    body: { ok: true, heartbeat: { cycle_age_s: 4, observation_age_s: 246 } } });
  assert.equal(r.state, 'OK');
});

test('health: anything unreadable is UNKNOWN, never OK', () => {
  const cases = [
    undefined,
    { error: 'unreachable or blocked (CORS)' },
    { status: 502, body: null },
    { status: 200, body: null },
    { status: 200, body: { ok: false, heartbeat: { cycle_age_s: 1, observation_age_s: 1 } } },
    { status: 200, body: { ok: true } },
    { status: 200, body: { ok: true, heartbeat: { cycle_age_s: null, observation_age_s: 3 } } },
    { status: 200, body: { ok: true, heartbeat: { cycle_age_s: '4', observation_age_s: 3 } } },
    { status: 200, body: { ok: true, heartbeat: { cycle_age_s: -1, observation_age_s: 3 } } },
  ];
  for (const c of cases) assert.equal(classifyHealth(c).state, 'UNKNOWN', JSON.stringify(c));
});

test('health: an age over the limit is STALE, and stale wins over a null partner', () => {
  const hb = (c, o) => ({ status: 200, body: { ok: true, heartbeat:
    { cycle_age_s: c, observation_age_s: o } } });
  assert.equal(classifyHealth(hb(3, HEARTBEAT_MAX_S)).state, 'OK');
  assert.equal(classifyHealth(hb(3, HEARTBEAT_MAX_S + 1)).state, 'STALE');
  assert.equal(classifyHealth(hb(null, 5000)).state, 'STALE');
});

test('health: a fetch that throws (CORS, offline) reads as UNKNOWN', async () => {
  const reading = await readHealth(async () => { throw new TypeError('Failed to fetch'); });
  assert.equal(classifyHealth(reading).state, 'UNKNOWN');
  const bad = await readHealth(async () => ({ status: 200, json: async () => { throw 1; } }));
  assert.equal(classifyHealth(bad).state, 'UNKNOWN');
});
