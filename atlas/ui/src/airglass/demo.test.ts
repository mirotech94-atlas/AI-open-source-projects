// The demo hand drives the real gesture engine, so these check that the
// script performs the one gesture -- pull out, slide, let go, give -- against
// the real layout, with a stand-in for the model the app would draw.
import { describe, expect, it } from "vitest";
import { demoFrame, DEMO_LENGTH } from "./demo";
import { GestureEngine, HandTracker, hitBoxes, type EngineEvent, type Pt, type TargetBox } from "./gestures";
import { centre, layoutFor, VIDEO_DEFAULT } from "./layout";

const view = { w: 1400, h: 820 };
const L = layoutFor(view, VIDEO_DEFAULT, 5);
const tileBox: TargetBox = {
  id: "tile:knot", kind: "tile",
  rect: { x: L.tray.x + L.tile.w + 14, y: L.tray.y, w: L.tile.w, h: L.tile.h },
};
const boxes: TargetBox[] = [tileBox, { id: "zone:atlas", kind: "zone", rect: L.zone }];

describe("demo script", () => {
  it("stays finite, has 21 landmarks per hand, and loops", () => {
    for (let t = 0; t < DEMO_LENGTH; t += 0.25) {
      for (const h of demoFrame(t, view).hands) {
        expect(h).toHaveLength(21);
        for (const p of h) expect(Number.isFinite(p.x) && Number.isFinite(p.y)).toBe(true);
      }
    }
    expect(demoFrame(1, view).hands).toEqual(demoFrame(1 + DEMO_LENGTH, view).hands);
  });

  it("walks the coach steps in order", () => {
    expect([1, 6, 10, 14].map((t) => demoFrame(t, view).step)).toEqual([1, 2, 2, 3]);
  });

  it("through the real engine: pulls a model out, holds, lets go, then gives it to ATLAS", () => {
    const tracker = new HandTracker();
    const engine = new GestureEngine();
    const seen: string[] = [];
    let model: Pt | null = null;       // what the app would draw
    let rest: Pt | null = null;        // where it was last left
    for (let t = 0; t < DEMO_LENGTH; t += 1 / 60) {
      const f = demoFrame(t, view, {
        tile: centre(tileBox.rect), trayTop: L.tray.y, zone: centre(L.zone), model: rest,
      });
      const out = engine.update(tracker.update(f.hands, t), t, {
        hit: (x, y) => hitBoxes(boxes, x, y), trayTop: L.tray.y,
        model: model ? { ...model, r: 90 } : null,
      });
      for (const e of out.events as EngineEvent[]) {
        seen.push("id" in e ? `${e.type}:${e.id}` : e.type === "release" ? `release${e.drop ? ":drop" : ""}` : e.type);
        if (e.type === "release" && e.drop) model = null;
      }
      if (out.phase === "HOLD" && out.owner) model = { ...out.owner.point };
      if (out.phase !== "HOLD" && model) rest = model;
    }
    expect(seen).toEqual([
      "pull-start:tile:knot", "bring-out:tile:knot", "release", "grab", "release:drop",
    ]);
  });
});
