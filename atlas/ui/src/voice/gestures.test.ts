import { describe, expect, it } from "vitest";
import { classify, GestureStream, type Gesture } from "./gestures";
import { INDEX_TIP, THUMB_TIP, type Point } from "./pinch";

// MediaPipe's 21-point layout, by finger:
//   0 wrist
//   1-4   thumb  (CMC, MCP, IP, TIP)
//   5-8   index  (MCP, PIP, DIP, TIP)
//   9-12  middle (MCP, PIP, DIP, TIP)
//   13-16 ring   (MCP, PIP, DIP, TIP)
//   17-20 pinky  (MCP, PIP, DIP, TIP)
const WRIST = 0;
const THUMB_IP = 3;
const INDEX_PIP = 6;
const MIDDLE_TIP = 12;
const MIDDLE_PIP = 10;
const RING_TIP = 16;
const RING_PIP = 14;
const PINKY_TIP = 20;
const PINKY_PIP = 18;

/** A full 21-point hand. Every joint starts curled into a loose fist near
 * the wrist; `overrides` replaces individual points to pose specific
 * fingers, the same shape pinch.test.ts uses for its own fixtures. */
function hand(overrides: Partial<Record<number, Point>> = {}): Point[] {
  const points: Point[] = new Array(21).fill(null).map(() => ({ x: 0.5, y: 0.65 }));
  points[WRIST] = { x: 0.5, y: 0.9 };
  // Thumb tucked in: tip no farther from the wrist (in x) than the IP joint,
  // and kept well clear of the curled fingertips at (0.5, 0.65) so an
  // ordinary fist is never mistaken for a pinch.
  points[THUMB_IP] = { x: 0.3, y: 0.78 };
  points[THUMB_TIP] = { x: 0.32, y: 0.75 };
  // Every other finger curled: tip below (greater y than) its pip.
  for (const [pip, tip] of [[INDEX_PIP, INDEX_TIP], [MIDDLE_PIP, MIDDLE_TIP],
    [RING_PIP, RING_TIP], [PINKY_PIP, PINKY_TIP]] as const) {
    points[pip] = { x: 0.5, y: 0.6 };
    points[tip] = { x: 0.5, y: 0.65 };
  }
  for (const [index, point] of Object.entries(overrides)) {
    if (point) points[Number(index)] = point;
  }
  return points;
}

function extend(pip: number, tip: number, x: number): [number, Point][] {
  return [[pip, { x, y: 0.6 }], [tip, { x, y: 0.3 }]];
}

function extendThumb(x = 0.2): [number, Point][] {
  return [[THUMB_IP, { x: 0.45, y: 0.75 }], [THUMB_TIP, { x, y: 0.6 }]];
}

describe("classify", () => {
  it("is none on a hand with fewer than 21 points", () => {
    expect(classify(new Array(9).fill({ x: 0, y: 0 }))).toBe("none");
  });

  it("is fist when every finger is curled and the thumb is tucked in", () => {
    expect(classify(hand())).toBe("fist");
  });

  it("is palm when all four fingers are extended", () => {
    const overrides = Object.fromEntries([
      ...extend(INDEX_PIP, INDEX_TIP, 0.35),
      ...extend(MIDDLE_PIP, MIDDLE_TIP, 0.45),
      ...extend(RING_PIP, RING_TIP, 0.55),
      ...extend(PINKY_PIP, PINKY_TIP, 0.65),
    ]);
    expect(classify(hand(overrides))).toBe("palm");
  });

  it("is point when only the index finger is extended", () => {
    const overrides = Object.fromEntries(extend(INDEX_PIP, INDEX_TIP, 0.4));
    expect(classify(hand(overrides))).toBe("point");
  });

  it("is two when index and middle are extended and ring/pinky are not", () => {
    const overrides = Object.fromEntries([
      ...extend(INDEX_PIP, INDEX_TIP, 0.4),
      ...extend(MIDDLE_PIP, MIDDLE_TIP, 0.5),
    ]);
    expect(classify(hand(overrides))).toBe("two");
  });

  it("is none for a partial pose that matches no named gesture", () => {
    // Only the ring finger extended -- not a gesture this classifier names.
    const overrides = Object.fromEntries(extend(RING_PIP, RING_TIP, 0.55));
    expect(classify(hand(overrides))).toBe("none");
  });

  it("is pinch when thumb and index tips are close, ahead of any finger "
    + "reading the geometry might otherwise suggest", () => {
    const overrides = {
      [THUMB_TIP]: { x: 0.4, y: 0.31 },
      [INDEX_TIP]: { x: 0.4, y: 0.3 },
      [INDEX_PIP]: { x: 0.4, y: 0.6 },
    };
    expect(classify(hand(overrides))).toBe("pinch");
  });

  it("uses the thumb's x-position relative to the wrist (4 vs 3), not just "
    + "the four fingers, so a thumbs-up is not read as a fist", () => {
    const overrides = Object.fromEntries(extendThumb());
    expect(classify(hand(overrides))).toBe("none");
  });
});

describe("GestureStream", () => {
  const CURSOR = { x: 0.5, y: 0.5 };

  it("does not enter cursor mode before the palm has been held 400ms", () => {
    const s = new GestureStream();
    expect(s.push("palm", CURSOR.x, CURSOR.y, 0)).toBeNull();
    expect(s.push("palm", CURSOR.x, CURSOR.y, 200)).toBeNull();
    expect(s.push("palm", CURSOR.x, CURSOR.y, 399)).toBeNull();
  });

  it("enters cursor mode on the frame the palm hold reaches 400ms", () => {
    const s = new GestureStream();
    s.push("palm", CURSOR.x, CURSOR.y, 0);
    s.push("palm", CURSOR.x, CURSOR.y, 200);
    expect(s.push("palm", CURSOR.x, CURSOR.y, 400))
      .toEqual({ kind: "cursor", x: CURSOR.x, y: CURSOR.y });
  });

  it("restarts the hold timer if the palm is interrupted", () => {
    const s = new GestureStream();
    s.push("palm", CURSOR.x, CURSOR.y, 0);
    s.push("none", CURSOR.x, CURSOR.y, 100);
    // Palm again, but the clock for THIS hold only starts now.
    s.push("palm", CURSOR.x, CURSOR.y, 150);
    expect(s.push("palm", CURSOR.x, CURSOR.y, 500)).toBeNull();
    expect(s.push("palm", CURSOR.x, CURSOR.y, 550))
      .toEqual({ kind: "cursor", x: CURSOR.x, y: CURSOR.y });
  });

  it("goes fist-while-cursor -> grab_start, then palm -> grab_end", () => {
    const s = new GestureStream();
    s.push("palm", CURSOR.x, CURSOR.y, 0);
    s.push("palm", CURSOR.x, CURSOR.y, 400); // now in cursor mode

    expect(s.push("fist", 0.4, 0.4, 450)).toEqual({ kind: "grab_start" });
    // Still fisted: dragging continues, reported as ordinary cursor moves.
    expect(s.push("fist", 0.35, 0.42, 500))
      .toEqual({ kind: "cursor", x: 0.35, y: 0.42 });
    expect(s.push("palm", 0.3, 0.45, 550)).toEqual({ kind: "grab_end" });
    // Back to plain cursor tracking, not another grab_end.
    expect(s.push("palm", 0.3, 0.45, 600))
      .toEqual({ kind: "cursor", x: 0.3, y: 0.45 });
  });

  it("emits a swipe when the palm crosses 0.35 of the frame in under 350ms", () => {
    const s = new GestureStream();
    expect(s.push("palm", 0.1, 0.5, 0)).toBeNull();
    expect(s.push("palm", 0.3, 0.5, 150)).toBeNull();
    expect(s.push("palm", 0.5, 0.5, 250))
      .toEqual({ kind: "swipe", dir: "right" });
  });

  it("reports the direction of a leftward swipe", () => {
    const s = new GestureStream();
    s.push("palm", 0.8, 0.5, 0);
    expect(s.push("palm", 0.4, 0.5, 200))
      .toEqual({ kind: "swipe", dir: "left" });
  });

  it("does not swipe on a slow drift across the same distance", () => {
    const s = new GestureStream();
    s.push("palm", 0.1, 0.5, 0);
    s.push("palm", 0.2, 0.5, 200);
    // 0.4 total movement, but the window (350ms) has long since slid past
    // the first sample by the time the distance is crossed.
    expect(s.push("palm", 0.5, 0.5, 800)).not.toEqual({ kind: "swipe", dir: "right" });
  });

  it("wakes on three consecutive pinch frames, only outside cursor mode "
    + "(Task 16's debounce, reused rather than re-invented)", () => {
    const s = new GestureStream();
    expect(s.push("pinch", 0.5, 0.5, 0)).toBeNull();
    expect(s.push("pinch", 0.5, 0.5, 60)).toBeNull();
    expect(s.push("pinch", 0.5, 0.5, 120)).toEqual({ kind: "wake" });
  });

  it("never wakes while a cursor or grab session is in progress -- a pinch "
    + "made while reaching for a tile must not also fire the wake word", () => {
    const s = new GestureStream();
    s.push("palm", 0.5, 0.5, 0);
    s.push("palm", 0.5, 0.5, 400); // cursor mode

    expect(s.push("pinch", 0.5, 0.5, 450)).toBeNull();
    expect(s.push("pinch", 0.5, 0.5, 500)).toBeNull();
    expect(s.push("pinch", 0.5, 0.5, 550)).toBeNull();
  });

  it("dismisses after the two-finger sign is held 600ms, once", () => {
    const s = new GestureStream();
    expect(s.push("two", 0.5, 0.5, 0)).toBeNull();
    expect(s.push("two", 0.5, 0.5, 300)).toBeNull();
    expect(s.push("two", 0.5, 0.5, 600)).toEqual({ kind: "dismiss" });
    // Still held: does not fire again on every subsequent frame.
    expect(s.push("two", 0.5, 0.5, 650)).toBeNull();
  });

  it("can dismiss again after the two sign is released and re-shown", () => {
    const s = new GestureStream();
    s.push("two", 0.5, 0.5, 0);
    s.push("two", 0.5, 0.5, 600);
    s.push("none", 0.5, 0.5, 700);
    s.push("two", 0.5, 0.5, 800);
    expect(s.push("two", 0.5, 0.5, 1400)).toEqual({ kind: "dismiss" });
  });

  it("drops back to idle on dismiss, so cursor mode must be re-earned", () => {
    const s = new GestureStream();
    s.push("palm", 0.5, 0.5, 0);
    s.push("palm", 0.5, 0.5, 400); // cursor mode
    s.push("two", 0.5, 0.5, 450);
    s.push("two", 0.5, 0.5, 1050); // dismiss fires, mode -> idle

    // A fresh, unheld palm does not resume cursor mode immediately.
    expect(s.push("palm", 0.5, 0.5, 1100)).toBeNull();
  });

  const gestures: Gesture[] = ["none", "palm", "fist", "pinch", "point", "two"];
  it.each(gestures)("never throws on a bare %s frame", (g) => {
    const s = new GestureStream();
    expect(() => s.push(g, 0.5, 0.5, 0)).not.toThrow();
  });
});
