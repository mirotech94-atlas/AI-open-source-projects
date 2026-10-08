// The overlay drawn over the picture each frame: a faint hand skeleton, a
// soft cursor dot on each hand's pinch point with a ring that fills as the
// pinch closes (full and gold when it is "held"), and the ghost of a tile
// being pulled out of the tray. A 2D canvas sized to the stage; it never
// takes pointer events. DOM text (the hint, the kinetics) is set by the
// controller.
import { at, ringFill, type Pt, type TrackedHand } from "./gestures";

const BONES: ReadonlyArray<readonly [number, number]> = [
  [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10],
  [10, 11], [11, 12], [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [0, 17],
  [17, 18], [18, 19], [19, 20],
];

export interface OverlayState {
  /** The hand whose cursor is the live one (owner, or closest to pinching). */
  cursorHand: number | null;
  overZone: boolean;
  /** Hovering something pinchable: the dot warms. */
  hovering: boolean;
  /** The tile being pulled: a ghost follows the hand until it leaves the tray. */
  ghost: { x: number; y: number; w: number; h: number; name: string } | null;
}

export class Overlay {
  private readonly ctx: CanvasRenderingContext2D | null;
  private w = 1;
  private h = 1;

  constructor(private readonly canvas: HTMLCanvasElement) {
    this.ctx = canvas.getContext("2d");
  }

  resize(w: number, h: number): void {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = w; this.h = h;
    this.canvas.width = Math.max(1, Math.round(w * dpr));
    this.canvas.height = Math.max(1, Math.round(h * dpr));
    this.ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  draw(hands: readonly TrackedHand[], s: OverlayState): void {
    const g = this.ctx;
    if (!g) return;
    g.clearRect(0, 0, this.w, this.h);
    if (s.ghost) this.ghost(g, s.ghost);
    for (const h of hands) {
      const lm = h.lm;
      g.lineWidth = 1.2;
      g.strokeStyle = "rgba(120, 240, 255, 0.32)";
      g.beginPath();
      for (const [a, b] of BONES) {
        const p = at(lm, a), q = at(lm, b);
        g.moveTo(p.x, p.y); g.lineTo(q.x, q.y);
      }
      g.stroke();
      this.cursor(g, h.point, h.pinchState.active ? 1 : ringFill(h.ratio), h.pinchState.active,
        h.id === s.cursorHand, s.overZone, s.hovering);
    }
  }

  /** A soft dot, and a ring that fills clockwise as the pinch closes. */
  private cursor(g: CanvasRenderingContext2D, p: Pt, fill: number, held: boolean,
                 live: boolean, overZone: boolean, hovering: boolean): void {
    const R = held ? 15 : 19;
    const tone = overZone && held ? "120, 255, 170" : held || (live && hovering) ? "255, 214, 102"
      : "160, 246, 255";
    const glow = g.createRadialGradient(p.x, p.y, 0, p.x, p.y, 11);
    glow.addColorStop(0, `rgba(${tone}, ${live ? 0.95 : 0.6})`);
    glow.addColorStop(1, `rgba(${tone}, 0)`);
    g.fillStyle = glow;
    g.beginPath(); g.arc(p.x, p.y, 11, 0, Math.PI * 2); g.fill();
    g.lineWidth = 1.5;
    g.strokeStyle = `rgba(${tone}, 0.35)`;
    g.beginPath(); g.arc(p.x, p.y, R, 0, Math.PI * 2); g.stroke();
    if (fill > 0) {
      g.lineWidth = held ? 4 : 3;
      g.strokeStyle = `rgba(${tone}, ${held ? 1 : 0.9})`;
      g.beginPath();
      g.arc(p.x, p.y, R, -Math.PI / 2, -Math.PI / 2 + fill * Math.PI * 2);
      g.stroke();
    }
  }

  private ghost(g: CanvasRenderingContext2D, t: NonNullable<OverlayState["ghost"]>): void {
    const x = t.x - t.w / 2, y = t.y - t.h / 2, r = 12;
    g.save();
    g.globalAlpha = 0.85;
    g.fillStyle = "rgba(10, 40, 52, 0.7)";
    g.strokeStyle = "rgba(255, 214, 102, 0.95)";
    g.lineWidth = 1.5;
    g.beginPath();
    g.moveTo(x + r, y); g.arcTo(x + t.w, y, x + t.w, y + t.h, r);
    g.arcTo(x + t.w, y + t.h, x, y + t.h, r); g.arcTo(x, y + t.h, x, y, r);
    g.arcTo(x, y, x + t.w, y, r); g.closePath();
    g.fill(); g.stroke();
    g.fillStyle = "rgba(233, 253, 255, 0.95)";
    g.font = "12px ui-monospace, Menlo, monospace";
    g.textAlign = "center";
    g.fillText(t.name, t.x, t.y + 4);
    g.restore();
  }
}
