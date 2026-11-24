// Can a real hand reach what Air Glass asks it to pinch?
//
// the owner, live with his own camera (2026-10-06): "his forefinger cannot pick a
// model". The demo and the old tests passed because their hand was drawn
// straight onto the stage and could sit anywhere -- wrist below the picture
// included. A real hand cannot: the tracker finds it by its palm. These
// tests build the hand in the camera's own picture (realhand.ts), map it
// through the same mirror and cover crop as the live path, and require the
// whole hand to be in frame while its pinch is on the target.
import { describe, expect, it } from "vitest";
import {
  GestureEngine, HandTracker, hitBoxes, pinchRatio, screenToVideo, TUNING, videoToScreen,
  type EngineEvent, type Pt, type Size, type TargetBox,
} from "./gestures";
import { centre, layoutFor, VIDEO_DEFAULT } from "./layout";
import { inPicture, rawHand, rng } from "./realhand";

/** The ATLAS Menu's window, the dashboard's stage, and a few others. */
const STAGES: Size[] = [
  { w: 1280, h: 800 }, { w: 1180, h: 700 }, { w: 1440, h: 900 },
  { w: 1000, h: 640 }, { w: 1920, h: 1080 },
];
const VIDEOS: Size[] = [VIDEO_DEFAULT, { w: 640, h: 480 }];

/** Is there a raised hand (palm 0.17 of the picture), whole in frame, whose
 * pinch is on `at`? Tries the right hand and the left. */
function reachable(at: Pt, view: Size, video: Size): boolean {
  return [false, true].some((left) => inPicture(rawHand({ at, view, video, pinch: 1, left })));
}

describe("root cause: the old tray was below a real hand's reach", () => {
  // The layout shipped in 5dcd114: tiles 92 px tall at the very bottom
  // (bottom: 12px, under the legend), buttons and zone 14 px from the edges.
  const old = (view: Size): { tile: Pt; plus: Pt; zone: Pt } => ({
    tile: { x: view.w * 0.4, y: view.h - 94 },
    plus: { x: 43, y: view.h * 0.46 - 70 },
    zone: { x: view.w - 89, y: view.h * 0.42 },
  });

  it("a tile in the old tray needs the wrist below the picture", () => {
    for (const view of STAGES) {
      const t = old(view).tile;
      const v = screenToVideo(t, VIDEO_DEFAULT, view).y;
      expect(v, `${view.w}x${view.h}`).toBeGreaterThan(0.85);
      expect(reachable(t, view, VIDEO_DEFAULT), `${view.w}x${view.h}`).toBe(false);
    }
  });

  it("so the hand vanished before its pinch reached the tile", () => {
    const view = STAGES[0] as Size;
    const t = old(view).tile;
    const raw = rawHand({ at: t, view, video: VIDEO_DEFAULT, pinch: 1 });
    const wrist = raw[0] as Pt;
    expect(wrist.y).toBeGreaterThan(1);   // the wrist is off the bottom of the picture
  });
});

describe("every target in the new layout is within reach", () => {
  for (const video of VIDEOS) {
    for (const view of STAGES) {
      it(`${view.w}x${view.h} stage, ${video.w}x${video.h} camera`, () => {
        const L = layoutFor(view, video, 5);
        const tiles = Array.from({ length: 5 }, (_, i) => ({
          x: L.tray.x + i * (L.tile.w + 14) + L.tile.w / 2, y: L.tray.y + L.tile.h / 2,
        }));
        // A whole tile, not just its middle: the top and bottom edges too.
        for (const c of tiles) {
          for (const dy of [-0.35, 0, 0.35]) {
            const p = { x: c.x, y: c.y + dy * L.tile.h };
            expect(reachable(p, view, video), `tile at ${p.x.toFixed(0)},${p.y.toFixed(0)}`).toBe(true);
          }
        }
        // The button column, top to bottom (+ and - at its two sides), and
        // the ATLAS zone, its middle and its inner edge.
        for (const p of [{ x: L.side.x + 30, y: L.side.y + 30 }, { x: L.side.x + L.side.w - 30, y: L.side.y + 30 },
          ...[0.35, 0.55, 0.75].map((f) => ({ x: L.side.x + L.side.w / 2, y: L.side.y + f * L.side.h }))]) {
          expect(reachable(p, view, video), `button at ${p.x.toFixed(0)},${p.y.toFixed(0)}`).toBe(true);
        }
        expect(reachable(centre(L.zone), view, video), "zone").toBe(true);
        expect(reachable({ x: L.zone.x + 20, y: L.zone.y + L.zone.h / 2 }, view, video), "zone edge").toBe(true);
        // And nothing overlaps: the tray sits below the column and the zone.
        expect(L.tray.y).toBeGreaterThan(view.h * 0.45);
        expect(L.tray.x + L.tray.w).toBeLessThanOrEqual(view.w);
      });
    }
  }

  it("a hand held further away (smaller) lets the tray sit lower; nearer pulls it up", () => {
    const view = STAGES[0] as Size;
    const far = layoutFor(view, VIDEO_DEFAULT, 5, 0.12 * 800);
    const near = layoutFor(view, VIDEO_DEFAULT, 5, 0.24 * 800);
    expect(near.tray.y).toBeLessThan(far.tray.y);
  });
});

describe("the pinch reads the same near, far and tilted", () => {
  const view = STAGES[0] as Size, video = VIDEO_DEFAULT;
  const at = { x: 640, y: 380 };
  const toStage = (raw: Pt[]): Pt[] => raw.map((p) => ({ x: p.x * 1280, y: p.y * 720, z: (p.z ?? 0) * 1280 }));

  it("closed reads as held, open as open, at every distance and tilt", () => {
    for (const palmFrac of [0.1, 0.17, 0.26]) {
      for (const pitch of [0, 0.5, 0.9]) {
        for (const zScale of [1, 0.3]) {
          const shut = pinchRatio(toStage(rawHand({ at, view, video, palmFrac, pitch, zScale, pinch: 1 })));
          const open = pinchRatio(toStage(rawHand({ at, view, video, palmFrac, pitch, zScale, pinch: 0 })));
          const label = `palm ${palmFrac}, pitch ${pitch}, z x${zScale}`;
          expect(shut, label).toBeLessThan(TUNING.pinchClose);
          expect(open, label).toBeGreaterThan(TUNING.pinchOpen);
        }
      }
    }
  });
});

describe("through the real tracker and engine, a real hand pulls a model out", () => {
  for (const view of [STAGES[0] as Size, STAGES[1] as Size]) {
    it(`${view.w}x${view.h}: approach, pinch over 300 ms with jitter, slide up`, () => {
      const video = VIDEO_DEFAULT;
      const L = layoutFor(view, video, 5);
      const boxes: TargetBox[] = Array.from({ length: 5 }, (_, i) => ({
        id: `tile:${i}`, kind: "tile",
        rect: { x: L.tray.x + i * (L.tile.w + 14), y: L.tray.y, w: L.tile.w, h: L.tile.h },
      }));
      const target = boxes[1] as TargetBox;
      const tc = centre(target.rect);
      const tracker = new HandTracker();
      const engine = new GestureEngine();
      const seed = rng(11);
      const events: EngineEvent[] = [];
      const step = (t: number, at: Pt, pinch: number): void => {
        // Two picture pixels of tracker noise on every landmark.
        const raw = rawHand({ at, view, video, pinch, jitter: 2, seed });
        expect(inPicture(raw), `hand left the picture at t=${t.toFixed(2)}`).toBe(true);
        const hand = raw.map((p) => videoToScreen(p, video, view, true));
        const out = engine.update(tracker.update([hand], t), t, {
          hit: (x, y) => hitBoxes(boxes, x, y), trayTop: L.tray.y,
        });
        events.push(...out.events);
      };
      let t = 0;
      const start = { x: view.w * 0.5, y: view.h * 0.35 };
      for (let i = 0; i <= 40; i++, t += 1 / 30) {          // move to the tile, open
        const k = i / 40;
        step(t, { x: start.x + (tc.x - start.x) * k, y: start.y + (tc.y - start.y) * k }, 0);
      }
      for (let i = 0; i <= 9; i++, t += 1 / 30) step(t, tc, i / 9);   // close over 300 ms
      for (let i = 0; i <= 30; i++, t += 1 / 30) {          // slide up out of the tray
        step(t, { x: tc.x, y: tc.y - i * 8 }, 1);
      }
      const kinds = events.map((e) => ("id" in e ? `${e.type}:${e.id}` : e.type));
      expect(kinds).toEqual([`pull-start:${target.id}`, `bring-out:${target.id}`]);
    });
  }
});
