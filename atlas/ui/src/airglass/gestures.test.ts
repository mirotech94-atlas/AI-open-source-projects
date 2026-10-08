// The one gesture (pinch-slide, either hand) and what makes it feel natural:
// hysteresis, the one-euro filter, the leash that holds a still hand still,
// the grab easing, the release glide and spin, and mirror-correct direction.
import { describe, expect, it } from "vitest";
import {
  Follow, GestureEngine, HandTracker, hintFor, hitBoxes, LEGEND, OneEuro, Pinch, pinchPoint,
  pinchRatio, releaseMotion, rollFromDrag, screenToVideo, Slack, stepFlight, TUNING, Velocity,
  videoToScreen, type EngineEvent, type EngineInput, type EngineOut, type Pt, type TargetBox,
} from "./gestures";
import { synthHand } from "./demo";
import { rng } from "./realhand";

const close = (a: number, b: number, eps = 1e-6): void => {
  expect(Math.abs(a - b), `${a} != ${b}`).toBeLessThan(eps);
};

// ---------------------------------------------------------------- pinch

describe("pinch", () => {
  it("holds below 0.25 of palm size and lets go only above 0.4", () => {
    const p = new Pinch();
    const seq = [0.9, 0.3, 0.24, 0.3, 0.39, 0.41, 0.3, 0.26, 0.2];
    expect(seq.map((r) => p.update(r).active))
      .toEqual([false, false, true, true, true, false, false, false, true]);
  });

  it("fires started/ended once per transition", () => {
    const p = new Pinch();
    expect(p.update(0.1).started).toBe(true);
    expect(p.update(0.1).started).toBe(false);
    expect(p.update(0.9).ended).toBe(true);
    expect(p.update(0.9).ended).toBe(false);
  });

  it("ratio is independent of hand size (near or far from the camera)", () => {
    for (const pinch of [0, 0.5, 1]) {
      const near = pinchRatio(synthHand({ x: 300, y: 400, size: 220, pinch }));
      const far = pinchRatio(synthHand({ x: 300, y: 400, size: 45, pinch }));
      close(near, far, 1e-9);
    }
  });

  it("noisy fingertip depth neither drops a held pinch nor fakes one", () => {
    // MediaPipe's z is its noisiest coordinate: +/-0.08 palm on both tips.
    const r = rng(9);
    const tracker = new HandTracker();
    const held: boolean[] = [];
    for (let i = 0; i < 240; i++) {
      const pinch = i < 30 ? 0 : 1;
      const lm = synthHand({ x: 500, y: 500, size: 120, pinch });
      for (const k of [4, 8]) {
        const p = lm[k];
        if (p) lm[k] = { ...p, z: (p.z ?? 0) + 0.16 * 120 * r() };
      }
      // a real closed pinch keeps a small gap between the tip landmarks
      const th = lm[4], ix = lm[8];
      if (pinch && th && ix) { lm[4] = { ...th, x: th.x + 9 }; lm[8] = { ...ix, x: ix.x - 9 }; }
      held.push(tracker.update([lm], i / 30).hands[0]?.pinchState.active ?? false);
    }
    expect(held.slice(0, 30).some(Boolean)).toBe(false);      // open: never pinched
    const firstHeld = held.indexOf(true);
    expect(firstHeld).toBeGreaterThan(29);
    expect(firstHeld).toBeLessThan(40);                         // engages within ~0.3 s
    expect(held.slice(firstHeld).every(Boolean)).toBe(true);   // and never lets go
  });

  it("the cursor is the pinch point, so it barely moves as the fingers close", () => {
    const open = pinchPoint(synthHand({ x: 100, y: 500, size: 100 }));
    const shut = pinchPoint(synthHand({ x: 100, y: 500, size: 100, pinch: 1 }));
    // the old index-tip cursor slid ~90 px here; this one stays within a fifth of a palm
    expect(Math.hypot(open.x - shut.x, open.y - shut.y)).toBeLessThan(20);
  });
});

// ---------------------------------------------------------------- mapping

describe("mirror and cover crop", () => {
  const video = { w: 1280, h: 720 }, view = { w: 1000, h: 1000 };

  it("maps the centre to the centre and round-trips", () => {
    const c = videoToScreen({ x: 0.5, y: 0.5, z: 0 }, video, view);
    close(c.x, 500, 1e-9); close(c.y, 500, 1e-9);
    const p = { x: 0.31, y: 0.77 };
    const back = screenToVideo(videoToScreen(p, video, view), video, view);
    close(back.x, p.x, 1e-9); close(back.y, p.y, 1e-9);
  });

  it("a hand moving to his right (x falling in the camera's picture) moves right on screen", () => {
    const a = videoToScreen({ x: 0.6, y: 0.5 }, video, view);
    const b = videoToScreen({ x: 0.5, y: 0.5 }, video, view);
    expect(b.x).toBeGreaterThan(a.x);
  });

  it("sliding right turns the front right; sliding down turns it down", () => {
    const r = rollFromDrag(300, 0, 1200);
    close(r.ax, 0);
    close(r.ay, TUNING.spinPerWidth * 0.25, 1e-9);
    const d = rollFromDrag(0, 120, 1200);
    expect(d.ax).toBeGreaterThan(0);   // about +x: the front (+z) goes down (-y)
    close(d.ay, 0);
  });
});

// ---------------------------------------------------------------- filters

describe("filters: still when still, quick when moving", () => {
  it("one-euro reduces jitter at rest", () => {
    const f = new OneEuro();
    const r = rng(7);
    let rawVar = 0, outVar = 0;
    for (let i = 0; i < 300; i++) {
      const raw = 500 + 6 * r();
      const out = f.filter(raw, i / 30);
      if (i > 30) { rawVar += (raw - 500) ** 2; outVar += (out - 500) ** 2; }
    }
    expect(outVar).toBeLessThan(rawVar / 3);
  });

  it("one-euro keeps up with a fast slide (lag under 40 px at 1500 px/s)", () => {
    const f = new OneEuro();
    let out = 0;
    for (let i = 0; i <= 30; i++) out = f.filter(1500 * (i / 30), i / 30);
    expect(1500 - out).toBeLessThan(40);
  });

  it("the leash ignores wobble inside its slack and follows a slow slide", () => {
    const s = new Slack(2.5);
    const r = rng(3);
    s.reset({ x: 100, y: 100 });
    for (let i = 0; i < 200; i++) {
      const p = s.update({ x: 100 + 3 * r(), y: 100 + 3 * r() });
      expect(Math.hypot(p.x - 100, p.y - 100)).toBeLessThan(1e-9);
    }
    let p = { x: 0, y: 0 };
    for (let i = 0; i <= 60; i++) p = s.update({ x: 100 + i * 0.5, y: 100 });  // 15 px/s
    close(p.x, 130 - 2.5, 1e-9);
  });

  it("release velocity reads the last tenth of a second", () => {
    const v = new Velocity(0.1);
    for (let i = 0; i <= 20; i++) v.push(i / 100, 2000 * (i / 100), 0);
    close(v.get().vx, 2000, 1e-6);
    const w = new Velocity(0.1);
    for (let i = 0; i <= 10; i++) w.push(i / 100, 3000 * (i / 100), 0);
    for (let i = 11; i <= 40; i++) w.push(i / 100, 300, 0);
    expect(w.get().speed).toBe(0);    // a pause before letting go leaves it still
  });
});

describe("held: eases in, holds still, turns like a ball", () => {
  it("eases into the fingers over the grab time, then sits there", () => {
    const f = new Follow();
    f.start({ x: 400, y: 300 }, { x: 460, y: 340 }, 0);
    const first = f.update({ x: 400, y: 300 }, 0.0, 1200);
    close(first.x, 460, 1e-9);                      // starts where the model was
    const mid = f.update({ x: 400, y: 300 }, TUNING.grabEaseS / 2, 1200);
    expect(mid.x).toBeLessThan(460);
    expect(mid.x).toBeGreaterThan(400);
    const done = f.update({ x: 400, y: 300 }, TUNING.grabEaseS, 1200);
    close(done.x, 400, 1e-9);
    close(done.y, 300, 1e-9);
  });

  it("with reduced motion it is in the fingers at once", () => {
    const f = new Follow();
    f.start({ x: 400, y: 300 }, { x: 460, y: 340 }, 0);
    close(f.update({ x: 400, y: 300 }, 0, 1200, true).x, 400, 1e-9);
  });

  it("a real hand held still (tracker noise) neither drifts nor turns the model", () => {
    const tracker = new HandTracker();
    const f = new Follow();
    const r = rng(5);
    const xs: number[] = [];
    let turn = 0;
    for (let i = 0; i < 180; i++) {
      const t = i / 60;
      const raw = synthHand({ x: 600, y: 600, size: 140, pinch: 1 })
        .map((p) => ({ ...p, x: p.x + 4 * r(), y: p.y + 4 * r() }));   // +/-2 px noise
      const h = tracker.update([raw], t).hands[0];
      if (!h) continue;
      if (i === 0) f.start(h.point, h.point, t);
      const o = f.update(h.point, t, 1280);
      if (i > 30) { xs.push(o.x); turn += Math.abs(o.ax) + Math.abs(o.ay); }
    }
    expect(Math.max(...xs) - Math.min(...xs)).toBeLessThan(1);
    expect(turn).toBeLessThan(0.02);   // radians over two seconds
  });

  it("let go: a short glide and a spin that both settle; nothing with reduced motion", () => {
    const m = releaseMotion({ vx: 900, vy: 0, speed: 900 }, 1200);
    expect(m.glide.vx).toBeGreaterThan(0);
    expect(m.spin.y).toBeGreaterThan(0);
    expect(Math.abs(m.spin.y)).toBeLessThanOrEqual(TUNING.maxSpin);
    let b = { x: 0, y: 0, vx: m.glide.vx, vy: 0 };
    for (let i = 0; i < 120; i++) b = stepFlight(b, 1 / 60);
    expect(Math.abs(b.vx)).toBeLessThan(1);          // settled within two seconds
    expect(b.x).toBeLessThan(1.1 * m.glide.vx / TUNING.glideDamping);   // ~v/damping, then still
    const still = releaseMotion({ vx: 900, vy: 0, speed: 900 }, 1200, true);
    expect(still).toEqual({ glide: { vx: 0, vy: 0 }, spin: { x: 0, y: 0 } });
  });
});

// ---------------------------------------------------------------- hit test

describe("hit boxes", () => {
  const boxes: TargetBox[] = [
    { id: "tile:a", kind: "tile", rect: { x: 100, y: 500, w: 150, h: 120 } },
    { id: "tile:b", kind: "tile", rect: { x: 264, y: 500, w: 150, h: 120 } },
    { id: "btn:bigger", kind: "button", rect: { x: 20, y: 200, w: 64, h: 64 } },
  ];
  it("counts a pinch a little outside the box, and the nearer box between two", () => {
    expect(hitBoxes(boxes, 120, 520)?.id).toBe("tile:a");
    expect(hitBoxes(boxes, 90, 560)?.id).toBe("tile:a");          // 10 px outside
    expect(hitBoxes(boxes, 255, 560)?.id).toBe("tile:a");         // 5 px from a, 9 from b
    expect(hitBoxes(boxes, 261, 560)?.id).toBe("tile:b");
    expect(hitBoxes(boxes, 52, 290)?.id).toBe("btn:bigger");      // 26 px below
    expect(hitBoxes(boxes, 600, 100)).toBeNull();
  });
  it("answers for points outside the window too (unlike elementFromPoint)", () => {
    const below = [{ id: "tile:z", kind: "tile" as const, rect: { x: 0, y: 5000, w: 100, h: 100 } }];
    expect(hitBoxes(below, 50, 5050)?.id).toBe("tile:z");
  });
});

// ---------------------------------------------------------------- engine

type Frame = { t: number; hands: Pt[][]; input?: EngineInput };

function run(frames: Frame[]): { events: Array<EngineEvent & { t: number }>; out: EngineOut | null } {
  const tracker = new HandTracker();
  const engine = new GestureEngine();
  const events: Array<EngineEvent & { t: number }> = [];
  let out: EngineOut | null = null;
  for (const f of frames) {
    out = engine.update(tracker.update(f.hands, f.t), f.t, f.input ?? {});
    events.push(...out.events.map((e) => ({ ...e, t: f.t })));
  }
  return { events, out };
}

/** A hand whose pinch point is at (px, py), closing over 0.3 s from t0. */
const handAt = (px: number, py: number, pinch: number, left = false, size = 120): Pt[] => {
  const probe = pinchPoint(synthHand({ x: 0, y: 0, size, pinch, left }));
  return synthHand({ x: px - probe.x, y: py - probe.y, size, pinch, left });
};
const ramp = (t: number, t0: number): number => Math.min(1, Math.max(0, (t - t0) / 0.3));

const tray: TargetBox[] = [
  { id: "tile:knot", kind: "tile", rect: { x: 400, y: 520, w: 150, h: 120 } },
  { id: "btn:bigger", kind: "button", rect: { x: 40, y: 200, w: 64, h: 64 } },
  { id: "zone:atlas", kind: "zone", rect: { x: 1050, y: 200, w: 164, h: 224 } },
];
const hit = (x: number, y: number): ReturnType<typeof hitBoxes> => hitBoxes(tray, x, y);
const model = { x: 640, y: 300, r: 80 };
const kinds = (es: EngineEvent[]): string[] => es.map((e) => ("id" in e ? `${e.type}:${e.id}` : e.type));

describe("gesture engine: pinch and slide", () => {
  it("pinch a tile and slide it up: the model comes out, held; let go: released", () => {
    const frames: Frame[] = [];
    let t = 0;
    for (; t < 0.5; t += 1 / 60) frames.push({ t, hands: [handAt(475, 580, ramp(t, 0.1))], input: { hit, trayTop: 520 } });
    for (let i = 0; i < 40; i++, t += 1 / 60) {
      frames.push({ t, hands: [handAt(475, 580 - i * 8, 1)], input: { hit, trayTop: 520 } });
    }
    for (let i = 0; i < 20; i++, t += 1 / 60) {
      frames.push({ t, hands: [handAt(475, 260, 1 - ramp(t, t - i / 60))], input: { hit, trayTop: 520, model } });
    }
    frames.push({ t, hands: [handAt(475, 260, 0)], input: { hit, trayTop: 520, model } });
    const { events, out } = run(frames);
    expect(kinds(events)).toEqual(["pull-start:tile:knot", "bring-out:tile:knot", "release"]);
    const out1 = events[1];
    expect(out1?.type === "bring-out" && out1.y < 520 - TUNING.pullOutPx).toBe(true);
    expect(out?.phase).toBe("IDLE");
  });

  it("let go inside the tray: nothing comes out", () => {
    const frames: Frame[] = [];
    let t = 0;
    for (; t < 0.5; t += 1 / 60) frames.push({ t, hands: [handAt(475, 580, ramp(t, 0.1))], input: { hit, trayTop: 520 } });
    for (let i = 0; i < 10; i++, t += 1 / 60) frames.push({ t, hands: [handAt(475, 580, 0)], input: { hit, trayTop: 520 } });
    expect(kinds(run(frames).events)).toEqual(["pull-start:tile:knot", "pull-cancel:tile:knot"]);
  });

  it("pinch the model: held; slide over ATLAS and let go: dropped", () => {
    const frames: Frame[] = [];
    let t = 0;
    for (; t < 0.5; t += 1 / 60) frames.push({ t, hands: [handAt(660, 320, ramp(t, 0.1))], input: { hit, model } });
    for (let i = 0; i <= 60; i++, t += 1 / 60) {
      frames.push({ t, hands: [handAt(660 + i * 7, 320, 1)], input: { hit, model } });
    }
    let held: EngineOut | null = null;
    const tracker = new HandTracker(), engine = new GestureEngine();
    const events: EngineEvent[] = [];
    for (const f of frames) { held = engine.update(tracker.update(f.hands, f.t), f.t, f.input); events.push(...held.events); }
    expect(held?.overZone).toBe(true);
    for (let i = 0; i < 12; i++, t += 1 / 60) {
      const out = engine.update(tracker.update([handAt(1080, 320, 1 - ramp(t, t - i / 60))], t), t, { hit, model });
      events.push(...out.events);
    }
    const last = events.at(-1);
    expect(kinds(events)).toEqual(["grab", "release"]);
    expect(last?.type === "release" && last.drop).toBe(true);
  });

  it("a pinch on empty space grabs nothing, even if it then slides onto the model", () => {
    const frames: Frame[] = [];
    let t = 0;
    for (; t < 0.5; t += 1 / 60) frames.push({ t, hands: [handAt(200, 450, ramp(t, 0.1))], input: { hit, model } });
    for (let i = 0; i < 40; i++, t += 1 / 60) frames.push({ t, hands: [handAt(200 + i * 11, 450 - i * 4, 1)], input: { hit, model } });
    const { events, out } = run(frames);
    expect(events).toEqual([]);
    expect(out?.phase).toBe("IDLE");
  });

  it("a pinch-tap on a button presses it", () => {
    const frames: Frame[] = [];
    for (let t = 0; t < 0.6; t += 1 / 60) frames.push({ t, hands: [handAt(72, 232, ramp(t, 0.1))], input: { hit, model } });
    expect(kinds(run(frames).events)).toEqual(["press:btn:bigger"]);
  });

  it("either hand; with both up the first to pinch owns it, and the other can only press buttons", () => {
    const frames: Frame[] = [];
    let t = 0;
    // left hand grabs the model first
    for (; t < 0.5; t += 1 / 60) {
      frames.push({ t, hands: [handAt(650, 310, ramp(t, 0.1), true), handAt(900, 500, 0)], input: { hit, model } });
    }
    // the right hand pinches on the model too: it must not take it
    for (let i = 0; i < 30; i++, t += 1 / 60) {
      frames.push({ t, hands: [handAt(650, 310, 1, true), handAt(640, 300, ramp(t, 0.5))], input: { hit, model } });
    }
    const { events, out } = run(frames);
    expect(kinds(events)).toEqual(["grab"]);
    expect(out?.phase).toBe("HOLD");
    expect(out?.owner?.id).toBe(1);   // still the left hand, the first one tracked
  });

  it("a hand that blurs out of the picture mid-slide is a release with its velocity", () => {
    const frames: Frame[] = [];
    let t = 0;
    for (; t < 0.5; t += 1 / 60) frames.push({ t, hands: [handAt(640, 300, ramp(t, 0.1))], input: { hit, model } });
    for (let i = 0; i < 15; i++, t += 1 / 60) frames.push({ t, hands: [handAt(640 + i * 20, 300, 1)], input: { hit, model } });
    frames.push({ t, hands: [], input: { hit, model } });
    const last = run(frames).events.at(-1);
    expect(last?.type).toBe("release");
    expect(last?.type === "release" && last.vx > 500).toBe(true);
  });

  it("the ring fills as the pinch closes and is full when held", () => {
    const tracker = new HandTracker(), engine = new GestureEngine();
    const rings: number[] = [];
    for (let i = 0; i <= 12; i++) {
      const out = engine.update(tracker.update([handAt(300, 300, i / 12)], i / 30), i / 30);
      rings.push(out.ring);
    }
    expect(rings[0]).toBe(0);
    for (let i = 1; i < rings.length; i++) expect(rings[i] ?? 0).toBeGreaterThanOrEqual((rings[i - 1] ?? 0) - 1e-9);
    expect(rings.at(-1)).toBe(1);
  });
});

describe("hint line", () => {
  const base = {
    source: "camera" as const, hands: 1, phase: "IDLE" as const, hasModel: true, overZone: false, hover: null,
  };
  it("follows what the hand is doing, and the legend is the one line", () => {
    expect(hintFor({ ...base, hands: 0 })).toMatch(/Raise a hand/);
    expect(hintFor({ ...base, hover: { kind: "tile", name: "Crystal" } })).toBe("Pinch Crystal and slide it up.");
    expect(hintFor({ ...base, hover: { kind: "button", name: "Bigger" } })).toMatch(/press Bigger/);
    expect(hintFor({ ...base, phase: "PULL" })).toMatch(/Slide it up/);
    expect(hintFor({ ...base, phase: "HOLD" })).toMatch(/Holding/);
    expect(hintFor({ ...base, phase: "HOLD", overZone: true })).toBe("Let go to give it to ATLAS.");
    expect(hintFor({ ...base, source: "none" })).toMatch(/No camera/);
    expect(hintFor(base)).toBe(LEGEND);
    expect(LEGEND).toBe("Pinch and slide (either hand). Say “ATLAS, look” to ask about what you're holding.");
  });
});
