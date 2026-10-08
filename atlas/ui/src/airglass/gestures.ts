// Gesture maths for Air Glass.
//
// Pure functions and small state machines: no DOM, no three.js, no network,
// so vitest runs them directly (gestures.test.ts). Every position here is in
// pixels of the STAGE (the Air Glass element, y down), after the mirror and
// the object-fit: cover crop have been applied by videoToScreen(). Time is
// in seconds.
//
// One gesture (2026-10-06, Miro: "I only want pinch-slide with the hand,
// doesn't matter which -- and make it natural"):
//   pinch a tile and slide it up out of the tray  -> the model comes out, held
//   pinch the model and slide                     -> it follows, and turns like a ball
//   let go                                        -> it stays, and settles
//   let go over ATLAS                             -> ATLAS takes it
//   pinch-tap a button (+, -, Ask ATLAS ...)      -> press it
// Either hand; with both up, the one that pinched first owns the grab.
// There is no hover-to-pick, no push/pull depth, no two-hand scale, no toss.
//
// Why picking failed with a real hand (Miro, 2026-10-06, "his forefinger
// cannot pick a model"): the tray sat in the bottom ~10% of the picture. A
// fingertip that low needs the wrist and palm below the camera's frame, and
// the hand model cannot find a hand whose palm it cannot see -- the demo's
// synthetic hand never minded. layout.ts now places every target where a
// raised hand, palm in frame, can put its pinch (reach.test.ts proves it).
// The cursor also used to slide ~0.65 hand sizes from the index tip to the
// pinch point as the fingers closed, so the pinch landed beside the tile it
// was aimed at; and the pinch ratio was measured in 2D, so a hand tilted
// toward the camera never read as pinched. All three are fixed below.

export interface Pt { x: number; y: number; z?: number }
export type Landmarks = readonly Pt[];

/** The MediaPipe hand landmarks we use, by index. */
export const LM = Object.freeze({
  WRIST: 0,
  THUMB_IP: 3, THUMB_TIP: 4,
  INDEX_MCP: 5, INDEX_PIP: 6, INDEX_TIP: 8,
  MIDDLE_MCP: 9, MIDDLE_PIP: 10, MIDDLE_TIP: 12,
  RING_MCP: 13, RING_PIP: 14, RING_TIP: 16,
  PINKY_MCP: 17, PINKY_PIP: 18, PINKY_TIP: 20,
});

export const TUNING = Object.freeze({
  // Pinch: thumb tip to index tip over palm size, in 3D, so it holds near the
  // camera and far from it, and with the hand tilted toward it.
  pinchClose: 0.25,     // ratio below which a pinch is held
  pinchOpen: 0.4,       // ratio above which it lets go (hysteresis between)
  ringFrom: 0.85,       // ratio at which the cursor ring starts to fill
  // One-euro filter for fingertips, in stage pixels: ~1 Hz at rest (no
  // jitter when held still), opening up with speed so a slide does not lag.
  euroMinCutoff: 1.0,
  euroBeta: 0.012,
  euroDCutoff: 1.0,
  holdSlackPx: 2.5,     // a held point ignores wobble smaller than this
  grabEaseS: 0.16,      // the model eases into the fingers over this
  velocityWindow: 0.1,  // seconds of history behind the release velocity
  glide: 0.22,          // share of the release velocity the model keeps
  glideDamping: 5.5,    // 1/s: how fast that glide settles
  spinPerWidth: 2 * Math.PI / 0.75, // radians of turn per stage width slid
  maxSpin: 6,           // rad/s cap on the spin a release can give
  pullOutPx: 28,        // slide this far above the tray to bring a model out
  hitPadPx: 28,         // a target counts this far outside its box
  modelPadPx: 56,       // and the model this far outside its own radius
  hoverGraceS: 0.25,    // a target slipped off this recently still counts
  minScale: 0.25,
  maxScale: 4,
});

const ORIGIN: Pt = { x: 0, y: 0, z: 0 };
/** One landmark, never undefined (a short array reads as the origin). */
export const at = (lm: Landmarks, i: number): Pt => lm[i] ?? ORIGIN;

// ---------------------------------------------------------------- geometry

export const dist = (a: Pt, b: Pt): number => Math.hypot(a.x - b.x, a.y - b.y);
export const dist3 = (a: Pt, b: Pt): number =>
  Math.hypot(a.x - b.x, a.y - b.y, (a.z ?? 0) - (b.z ?? 0));
export const mid = (a: Pt, b: Pt): Pt => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
export const clamp = (v: number, lo: number, hi: number): number =>
  Math.min(hi, Math.max(lo, v));
export const smoothstep = (t: number): number => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));

export interface Size { w: number; h: number }
export interface Rect { x: number; y: number; w: number; h: number }

/** The cover-fit of a video in a view: scale and offsets, in view pixels. */
export function coverFit(video: Size, view: Size): { s: number; ox: number; oy: number } {
  const s = Math.max(view.w / video.w, view.h / video.h);
  return { s, ox: (view.w - video.w * s) / 2, oy: (view.h - video.h * s) / 2 };
}

/**
 * Map one MediaPipe landmark (normalised to the VIDEO frame, un-mirrored,
 * x right, y down, z relative depth on the x scale) to stage pixels, given
 * that the video is shown with object-fit: cover and, for a selfie view,
 * mirrored. Moving the hand to his right moves it right on screen.
 */
export function videoToScreen(p: Pt, video: Size, view: Size, mirror = true): Pt {
  const { s, ox, oy } = coverFit(video, view);
  const nx = mirror ? 1 - p.x : p.x;
  return { x: nx * video.w * s + ox, y: p.y * video.h * s + oy, z: (p.z ?? 0) * video.w * s };
}

/** The inverse of videoToScreen for x and y: a stage point in video terms. */
export function screenToVideo(p: Pt, video: Size, view: Size, mirror = true): Pt {
  const { s, ox, oy } = coverFit(video, view);
  const nx = (p.x - ox) / (video.w * s);
  return { x: mirror ? 1 - nx : nx, y: (p.y - oy) / (video.h * s) };
}

/**
 * Palm size, in 3D: wrist to middle-finger MCP, or the knuckle span scaled to
 * the same length, whichever is longer. Tilting the hand toward the camera
 * foreshortens the first and not the second; turning it sideways the other
 * way round. MediaPipe's z is noisy and under-scaled, so 3D alone is not
 * enough.
 */
export const palmSize = (lm: Landmarks): number => Math.max(
  1e-6,
  dist3(at(lm, LM.WRIST), at(lm, LM.MIDDLE_MCP)),
  1.45 * dist3(at(lm, LM.INDEX_MCP), at(lm, LM.PINKY_MCP)),
);

/** Thumb-tip to index-tip distance over palm size (3D): ~1 open, ~0 pinched. */
export const pinchRatio = (lm: Landmarks): number =>
  dist3(at(lm, LM.THUMB_TIP), at(lm, LM.INDEX_TIP)) / palmSize(lm);

/** The point a pinch holds, and the cursor: halfway between thumb and index
 * tips. The same point open or closed, so the dot is where the pinch lands. */
export const pinchPoint = (lm: Landmarks): Pt => mid(at(lm, LM.THUMB_TIP), at(lm, LM.INDEX_TIP));

/** 0..1, how far the pinch has closed toward "held", for the cursor ring. */
export const ringFill = (ratio: number): number =>
  clamp((TUNING.ringFrom - ratio) / (TUNING.ringFrom - TUNING.pinchClose), 0, 1);

/**
 * Slide-to-turn: how far the held point moved this frame becomes a turn,
 * like rolling a ball under the hand. Sliding right turns it about the
 * vertical axis so its front goes right; sliding down, about the horizontal
 * axis so its front goes down. Radians, in world axes (y up).
 */
export function rollFromDrag(dx: number, dy: number, viewW: number): { ax: number; ay: number } {
  const k = TUNING.spinPerWidth / Math.max(1, viewW);
  return { ax: dy * k, ay: dx * k };
}

// ---------------------------------------------------------------- filters

/** One-euro filter (Casiez et al. 2012): smooth at rest, responsive in motion. */
export class OneEuro {
  private x: number | null = null;
  private dx = 0;
  private t: number | null = null;
  constructor(
    private readonly minCutoff: number = TUNING.euroMinCutoff,
    private readonly beta: number = TUNING.euroBeta,
    private readonly dCutoff: number = TUNING.euroDCutoff,
  ) {}
  reset(): void { this.x = null; this.dx = 0; this.t = null; }
  static alpha(cutoff: number, dt: number): number {
    const tau = 1 / (2 * Math.PI * cutoff);
    return 1 / (1 + tau / dt);
  }
  filter(x: number, t: number): number {
    if (this.t === null || this.x === null) { this.x = x; this.t = t; this.dx = 0; return x; }
    const dt = Math.max(t - this.t, 1e-3);
    this.t = t;
    const ad = OneEuro.alpha(this.dCutoff, dt);
    this.dx = ad * ((x - this.x) / dt) + (1 - ad) * this.dx;
    const a = OneEuro.alpha(this.minCutoff + this.beta * Math.abs(this.dx), dt);
    this.x = a * x + (1 - a) * this.x;
    return this.x;
  }
}

/**
 * A point on a short leash: it moves only when the input pulls more than
 * `slack` pixels away, and then by the excess. Wobble inside the slack is
 * ignored entirely, so a model held still is still, while a slow deliberate
 * slide is followed exactly (less a constant `slack` lag).
 */
export class Slack {
  private p: Pt | null = null;
  constructor(private readonly slack: number = TUNING.holdSlackPx) {}
  reset(p: Pt | null = null): void { this.p = p ? { x: p.x, y: p.y } : null; }
  update(q: Pt): Pt {
    if (!this.p) { this.p = { x: q.x, y: q.y }; return { ...this.p }; }
    const dx = q.x - this.p.x, dy = q.y - this.p.y;
    const d = Math.hypot(dx, dy);
    if (d > this.slack) {
      const k = (d - this.slack) / d;
      this.p = { x: this.p.x + dx * k, y: this.p.y + dy * k };
    }
    return { ...this.p };
  }
}

export interface PinchState { active: boolean; started: boolean; ended: boolean }

/** Pinch with hysteresis: held below `close`, let go only above `open`. */
export class Pinch {
  active = false;
  constructor(private readonly close: number = TUNING.pinchClose,
              private readonly open: number = TUNING.pinchOpen) {}
  update(ratio: number): PinchState {
    const was = this.active;
    if (!was && ratio < this.close) this.active = true;
    else if (was && ratio > this.open) this.active = false;
    return { active: this.active, started: !was && this.active, ended: was && !this.active };
  }
}

export interface Vel { vx: number; vy: number; speed: number }

/** Release velocity from the last `window` seconds of samples (px/s). */
export class Velocity {
  private s: Array<{ t: number; x: number; y: number }> = [];
  constructor(private readonly window: number = TUNING.velocityWindow) {}
  reset(): void { this.s = []; }
  push(t: number, x: number, y: number): void {
    this.s.push({ t, x, y });
    while (this.s.length > 2 && t - (this.s[0]?.t ?? t) > this.window) this.s.shift();
  }
  get(): Vel {
    const a = this.s[0], b = this.s[this.s.length - 1];
    if (!a || !b || this.s.length < 2) return { vx: 0, vy: 0, speed: 0 };
    const dt = b.t - a.t;
    if (dt < 1e-3) return { vx: 0, vy: 0, speed: 0 };
    const vx = (b.x - a.x) / dt, vy = (b.y - a.y) / dt;
    return { vx, vy, speed: Math.hypot(vx, vy) };
  }
}

export interface Body { x: number; y: number; vx: number; vy: number }

/** Advance a gliding body (no gravity): x += v dt, v decays exponentially. */
export function stepFlight(body: Body, dt: number, damping: number = TUNING.glideDamping): Body {
  const k = Math.exp(-damping * dt);
  return { x: body.x + body.vx * dt, y: body.y + body.vy * dt, vx: body.vx * k, vy: body.vy * k };
}

/** What a release leaves behind: a short glide and a spin that settles.
 * With reduced motion, neither: it stops where it was let go. */
export function releaseMotion(v: Vel, viewW: number, reducedMotion = false):
    { glide: { vx: number; vy: number }; spin: { x: number; y: number } } {
  if (reducedMotion) return { glide: { vx: 0, vy: 0 }, spin: { x: 0, y: 0 } };
  const r = rollFromDrag(v.vx, v.vy, viewW);
  return {
    glide: { vx: v.vx * TUNING.glide, vy: v.vy * TUNING.glide },
    spin: { x: clamp(r.ax, -TUNING.maxSpin, TUNING.maxSpin), y: clamp(r.ay, -TUNING.maxSpin, TUNING.maxSpin) },
  };
}

/** How far the grab has eased the model into the fingers, 0..1. */
export const grabEase = (sinceS: number, reducedMotion = false): number =>
  (reducedMotion ? 1 : smoothstep(sinceS / TUNING.grabEaseS));

/**
 * The held model following the hand. At the grab the model is wherever it
 * was; over grabEaseS it eases into the fingers and then stays there. The
 * point goes through a Slack first, so a hand held still holds the model
 * still -- no drift and no turn -- and the turn comes from the same leashed
 * motion as the move, so the two never disagree.
 */
export class Follow {
  private readonly slack = new Slack();
  private last: Pt = ORIGIN;
  private offset: Pt = ORIGIN;
  private t0 = 0;
  start(point: Pt, model: Pt, t: number): void {
    this.slack.reset(point);
    this.last = { x: point.x, y: point.y };
    this.offset = { x: model.x - point.x, y: model.y - point.y };
    this.t0 = t;
  }
  update(point: Pt, t: number, viewW: number, reducedMotion = false):
      { x: number; y: number; ax: number; ay: number } {
    const p = this.slack.update(point);
    const k = 1 - grabEase(t - this.t0, reducedMotion);
    const r = rollFromDrag(p.x - this.last.x, p.y - this.last.y, viewW);
    this.last = p;
    return { x: p.x + this.offset.x * k, y: p.y + this.offset.y * k, ax: r.ax, ay: r.ay };
  }
}

// ------------------------------------------------------------ hand tracker

export interface TrackedHand {
  id: number;
  lm: Pt[];
  ratio: number;
  pinchState: PinchState;
  /** The pinch point (thumb-index midpoint): the cursor. */
  point: Pt;
  size: number;
  vel: Velocity;
}

interface HandSlot {
  id: number;
  pinch: Pinch;
  vel: Velocity;
  f: Array<[OneEuro, OneEuro, OneEuro]>;
  hand: TrackedHand | null;
}

export interface Tracked { hands: TrackedHand[]; lost: TrackedHand[] }

const euro3 = (): [OneEuro, OneEuro, OneEuro] =>
  [new OneEuro(), new OneEuro(), new OneEuro(1.0, 0.004)];

/**
 * Keeps up to two hands' identities stable from frame to frame (nearest
 * wrist), smooths every landmark with a one-euro filter, and runs a pinch
 * detector per hand. Input: arrays of 21 points already in stage pixels.
 */
export class HandTracker {
  private slots: HandSlot[] = [];
  private nextId = 1;

  get hands(): TrackedHand[] {
    return this.slots.flatMap((s) => (s.hand ? [s.hand] : []));
  }

  reset(): void { this.slots = []; }

  update(rawHands: readonly Landmarks[], t: number): Tracked {
    const prev = this.slots;
    const used = new Set<number>();
    const next: HandSlot[] = [];
    for (const raw of rawHands.slice(0, 2)) {
      let best: HandSlot | null = null, bestD = Infinity;
      for (const s of prev) {
        if (used.has(s.id) || !s.hand) continue;
        const d = dist(at(s.hand.lm, LM.WRIST), at(raw, LM.WRIST));
        if (d < bestD) { bestD = d; best = s; }
      }
      // A wrist that jumped more than 3 palm sizes is a new hand, not this one.
      if (best?.hand && bestD > 3 * best.hand.size) best = null;
      const slot: HandSlot = best ?? {
        id: this.nextId++, pinch: new Pinch(), vel: new Velocity(), f: raw.map(euro3), hand: null,
      };
      used.add(slot.id);
      const lm = raw.map((p, i) => {
        const f = slot.f[i] ?? (slot.f[i] = euro3());
        return { x: f[0].filter(p.x, t), y: f[1].filter(p.y, t), z: f[2].filter(p.z ?? 0, t) };
      });
      const ratio = pinchRatio(lm);
      const pinchState = slot.pinch.update(ratio);
      const point = pinchPoint(lm);
      slot.vel.push(t, point.x, point.y);
      slot.hand = { id: slot.id, lm, ratio, pinchState, point, size: palmSize(lm), vel: slot.vel };
      next.push(slot);
    }
    const lost = prev.filter((s) => !used.has(s.id)).flatMap((s) => (s.hand ? [s.hand] : []));
    this.slots = next;
    return { hands: this.hands, lost };
  }
}

// ---------------------------------------------------------------- hit test

/** What is under a stage point: a tray tile, an on-screen button, or the
 * ATLAS drop zone. The model itself is tested separately (EngineInput.model). */
export type TargetKind = "tile" | "button" | "zone";
export interface Target { id: string; kind: TargetKind }
export type HitTest = (x: number, y: number) => Target | null;
export interface TargetBox extends Target { rect: Rect }

/**
 * Hit-test cached target boxes, each grown by `pad` pixels: a fingertip is
 * not a mouse, and a box no bigger than its drawing is too small to land a
 * pinch on. Inside a box wins; between padded boxes, the nearest edge wins.
 * Works for any stage point, unlike elementFromPoint, which answers null for
 * anything scrolled out of the window.
 */
export function hitBoxes(boxes: readonly TargetBox[], x: number, y: number,
                         pad: number = TUNING.hitPadPx): Target | null {
  let best: TargetBox | null = null, bestD = Infinity;
  for (const b of boxes) {
    const dx = Math.max(b.rect.x - x, 0, x - (b.rect.x + b.rect.w));
    const dy = Math.max(b.rect.y - y, 0, y - (b.rect.y + b.rect.h));
    const d = Math.hypot(dx, dy);
    if (d <= pad && d < bestD) { best = b; bestD = d; }
  }
  return best ? { id: best.id, kind: best.kind } : null;
}

// ---------------------------------------------------------- gesture engine

/** The model on show, as the stage draws it: centre and radius in pixels. */
export interface ModelDisc { x: number; y: number; r: number }

export type EngineEvent =
  /** A pinch-tap on a button. */
  | { type: "press"; id: string }
  /** A tile pinched: a ghost of it follows the hand until it leaves the tray. */
  | { type: "pull-start"; id: string }
  /** Slid up out of the tray: show that model at (x, y); it is now held. */
  | { type: "bring-out"; id: string; x: number; y: number }
  /** Let go inside the tray: nothing comes out. */
  | { type: "pull-cancel"; id: string }
  /** The model pinched: it is now held. */
  | { type: "grab"; x: number; y: number }
  | {
    type: "release"; vx: number; vy: number; x: number; y: number;
    /** Let go over the ATLAS zone: ATLAS takes it. */
    drop: boolean;
  };

export type Phase = "IDLE" | "PULL" | "HOLD";

export interface EngineInput {
  hit?: HitTest;
  /** The model on show, or null when there is nothing to grab. */
  model?: ModelDisc | null;
  /** Stage y of the tray's top edge. A pulled tile that rises this far
   * (less pullOutPx) brings its model out. Null: 80 px of rise does. */
  trayTop?: number | null;
}

export interface EngineOut {
  phase: Phase;
  events: EngineEvent[];
  /** The hand that owns the grab or pull, if any. */
  owner: TrackedHand | null;
  /** The cursor shown: the owner's pinch point, else the most-closed hand's. */
  cursor: Pt | null;
  /** 0..1 for that cursor's ring; 1 and `held` when the pinch has closed. */
  ring: number;
  held: boolean;
  /** What the cursor is over ("model" when it is over the model). */
  hover: Target | { id: "model"; kind: "model" } | null;
  /** Holding the model over the ATLAS zone. */
  overZone: boolean;
  /** The tile being pulled, and where its ghost is. */
  pull: { id: string; x: number; y: number } | null;
}

export class GestureEngine {
  phase: Phase = "IDLE";
  private owner: number | null = null;
  private pullId: string | null = null;
  private pullFrom: Pt | null = null;
  private readonly spent = new Set<number>(); // pinches that pressed or missed
  private lastHands = new Map<number, TrackedHand>();
  private lastHover = new Map<number, { target: Target; t: number }>();
  private overZone = false;

  update({ hands, lost = [] }: Tracked, t: number, input: EngineInput = {}): EngineOut {
    const hit = input.hit ?? ((): Target | null => null);
    const model = input.model ?? null;
    const events: EngineEvent[] = [];
    const byId = new Map(hands.map((h) => [h.id, h]));
    const onModel = (p: Pt): boolean => model !== null
      && Math.hypot(p.x - model.x, p.y - model.y) <= model.r + TUNING.modelPadPx;

    for (const h of hands) {
      const under = hit(h.point.x, h.point.y);
      if (under && under.kind !== "zone") this.lastHover.set(h.id, { target: under, t });
    }

    // The owner let go, or its hand left the picture (a hand that blurs out
    // mid-slide is a release, with the velocity it had when last seen).
    if (this.owner !== null) {
      const h = byId.get(this.owner);
      if (!h || !h.pinchState.active) {
        const last = h ?? lost.find((x) => x.id === this.owner) ?? this.lastHands.get(this.owner);
        const v = last ? last.vel.get() : { vx: 0, vy: 0, speed: 0 };
        const p = last?.point ?? ORIGIN;
        if (this.phase === "PULL" && this.pullId) {
          events.push({ type: "pull-cancel", id: this.pullId });
        } else if (this.phase === "HOLD") {
          events.push({
            type: "release", vx: v.vx, vy: v.vy, x: p.x, y: p.y,
            drop: this.overZone || hit(p.x, p.y)?.kind === "zone",
          });
        }
        this.owner = null;
        this.pullId = null;
        this.pullFrom = null;
        this.phase = "IDLE";
        this.overZone = false;
      }
    }

    // New pinches. A pinch that starts on a button presses it; on a tile,
    // pulls it; on the model, holds it. A second hand can still press a
    // button while the first holds, but never takes the grab away.
    for (const h of hands) {
      if (!h.pinchState.started) continue;
      let target = hit(h.point.x, h.point.y);
      const recent = this.lastHover.get(h.id);
      if ((!target || target.kind === "zone") && recent && t - recent.t <= TUNING.hoverGraceS) {
        target = recent.target;
      }
      if (target?.kind === "button") {
        events.push({ type: "press", id: target.id });
        this.spent.add(h.id);
      } else if (this.owner !== null) {
        this.spent.add(h.id);
      } else if (target?.kind === "tile") {
        this.owner = h.id;
        this.phase = "PULL";
        this.pullId = target.id;
        this.pullFrom = { ...h.point };
        events.push({ type: "pull-start", id: target.id });
      } else if (onModel(h.point)) {
        this.owner = h.id;
        this.phase = "HOLD";
        events.push({ type: "grab", x: h.point.x, y: h.point.y });
      } else {
        this.spent.add(h.id);
      }
    }
    for (const h of hands) if (!h.pinchState.active) this.spent.delete(h.id);
    for (const h of lost) { this.spent.delete(h.id); this.lastHover.delete(h.id); }

    const owner = this.owner !== null ? byId.get(this.owner) ?? null : null;

    // A pulled tile that has risen out of the tray brings its model out.
    if (this.phase === "PULL" && owner && this.pullId && this.pullFrom) {
      const top = input.trayTop ?? null;
      const out = top !== null ? owner.point.y < top - TUNING.pullOutPx
        : this.pullFrom.y - owner.point.y > 80;
      if (out) {
        events.push({ type: "bring-out", id: this.pullId, x: owner.point.x, y: owner.point.y });
        this.phase = "HOLD";
        this.pullId = null;
        this.pullFrom = null;
      }
    }

    this.overZone = this.phase === "HOLD" && owner !== null
      && hit(owner.point.x, owner.point.y)?.kind === "zone";

    // The cursor: the owner, else whichever hand is closest to pinching.
    const shown = owner ?? [...hands].sort((a, b) => a.ratio - b.ratio)[0] ?? null;
    let hover: EngineOut["hover"] = null;
    if (shown && this.phase === "IDLE") {
      const under = hit(shown.point.x, shown.point.y);
      if (under && under.kind !== "zone") hover = under;
      else if (onModel(shown.point)) hover = { id: "model", kind: "model" };
    }

    this.lastHands = new Map(hands.map((h) => [h.id, h]));
    return {
      phase: this.phase, events, owner,
      cursor: shown ? shown.point : null,
      ring: shown ? (shown.pinchState.active ? 1 : ringFill(shown.ratio)) : 0,
      held: Boolean(shown?.pinchState.active),
      hover,
      overZone: this.overZone,
      pull: this.phase === "PULL" && owner && this.pullId
        ? { id: this.pullId, x: owner.point.x, y: owner.point.y } : null,
    };
  }
}

// ------------------------------------------------------------------- hint

export interface HintInput {
  source: "camera" | "demo" | "none";
  hands: number;
  phase: Phase;
  hasModel: boolean;
  overZone: boolean;
  /** What the cursor is over, by name ("Torus knot", "Bigger", "model"). */
  hover: { kind: TargetKind | "model"; name: string } | null;
}

export const LEGEND = "Pinch and slide (either hand). Say “ATLAS, look” to ask about what you're holding.";

/** The one-line hint under the stage, following what the hand is doing. */
export function hintFor(s: HintInput): string {
  if (s.source === "none") return "No camera. Start the demo, or allow the camera and try again.";
  if (s.phase === "PULL") return "Slide it up out of the tray to bring it out.";
  if (s.phase === "HOLD") {
    if (s.overZone) return "Let go to give it to ATLAS.";
    return "Holding. Slide to move and turn it; let go to leave it, or over ATLAS to give it.";
  }
  if (s.hands === 0) return "Raise a hand so the camera can see it.";
  if (s.hover?.kind === "tile") return `Pinch ${s.hover.name} and slide it up.`;
  if (s.hover?.kind === "button") return `Pinch to press ${s.hover.name}.`;
  if (s.hover?.kind === "model") return "Pinch to hold it.";
  return LEGEND;
}
