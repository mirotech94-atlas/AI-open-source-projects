/**
 * The Brain map's geometry, as pure functions, in the same vocabulary as
 * the agency map (`neuralLayout.ts`): a small core at the centre, neurons
 * around it, spokes inward, labels on the side away from the core.
 *
 * The picture: the Knowledge Core is the centre. The other departments sit
 * on one ring around it, each a cluster -- its own node (the department,
 * named with its head) and a small fan of seats (its agents and jobs)
 * facing outward. A founder node, YOU, sits above the ring and is wired to
 * every department that holds a Tier 2 action: the ones that cannot finish
 * without your sign-off. Nothing else is drawn; nothing is inferred.
 *
 * At phone width the rings do not fit labelled, so the compact frame stacks
 * it instead: YOU, the core, then the departments two to a row, each with
 * its seats in a small grid under it.
 */
import type { Brain, BrainDepartment, BrainSeat } from "../api";
import { ringPoints, sideFor, variantFor, type Side, type Variant } from "./neuralLayout";

export { variantFor };

export interface BrainFrame { w: number; h: number; cx: number; cy: number; rx: number; ry: number }

export const BRAIN_WIDE: BrainFrame = { w: 1320, h: 800, cx: 660, cy: 430, rx: 400, ry: 250 };

export const BRAIN_COMPACT = {
  w: 400, youY: 34, coreY: 118, firstRow: 214, cols: 2, colX: [104, 296] as const,
  seatCols: 5, seatGap: 20, seatTop: 30, rowPad: 70,
};

export type NodeKind = "you" | "core" | "dept" | "seat";

export interface BrainNode {
  id: string;
  kind: NodeKind;
  dept: string | null;
  seat: BrainSeat | null;
  x: number; y: number;
  px: number; py: number;
  side: Side;
  size: number;
}

export interface BrainLink { id: string; kind: "spoke" | "seat" | "signoff"; d: string; dept: string }

export interface BrainLayoutResult {
  frame: BrainFrame;
  variant: Variant;
  you: BrainNode;
  core: BrainNode;
  depts: BrainNode[];
  seats: BrainNode[];
  links: BrainLink[];
  byId: Map<string, BrainNode>;
}

const round = (n: number): number => Math.round(n * 10) / 10;

const INWARD: Record<Side, Side> = { left: "right", right: "left", top: "bottom", bottom: "top" };

/** A seat's side in px: 7 idle, growing with the square root of its runs,
 *  capped at 16 -- the same idea as a neuron's, one size smaller. */
export function seatSize(ran: number | null): number {
  const runs = ran !== null && Number.isFinite(ran) && ran > 0 ? ran : 0;
  return Math.min(16, Math.round(7 + Math.sqrt(runs) * 1.6));
}

/** A department node's side: by how many seats it has. */
export function deptSize(d: Pick<BrainDepartment, "agents" | "jobs">): number {
  return Math.min(26, 14 + (d.agents.length + d.jobs.length));
}

/** `n` angles fanned around `base`, `step` apart, centred. */
export function fan(n: number, base: number, step: number): number[] {
  return Array.from({ length: n }, (_, i) => base + (i - (n - 1) / 2) * step);
}

export function brainLayout(brain: Brain, variant: Variant = "wide"): BrainLayoutResult {
  const coreDept = brain.departments.find((d) => d.core) ?? null;
  const ring = brain.departments.filter((d) => !d.core);
  const signoff = new Set(brain.departments.filter((d) => d.tier2_actions > 0).map((d) => d.key));
  const frame = variant === "wide" ? BRAIN_WIDE : compactFrame(ring, coreDept);

  const node = (id: string, kind: NodeKind, x: number, y: number, side: Side, size: number,
                dept: string | null = null, seat: BrainSeat | null = null): BrainNode => ({
    id, kind, dept, seat, x: round(x), y: round(y),
    px: round((x / frame.w) * 100), py: round((y / frame.h) * 100), side, size,
  });

  const depts: BrainNode[] = [];
  const seats: BrainNode[] = [];
  let you: BrainNode;
  let core: BrainNode;

  if (variant === "wide") {
    const { cx, cy, rx, ry } = frame;
    you = node("you", "you", cx, 44, "right", 18);
    core = node(coreDept ? `dept:${coreDept.key}` : "core", "core", cx, cy, "bottom", 22,
                coreDept?.key ?? null);
    // Start half a step off 12 o'clock so no department sits under YOU.
    const n = ring.length;
    const pts = ringPoints(n, rx, ry, -Math.PI / 2 + (n ? Math.PI / n : 0));
    ring.forEach((d, i) => {
      const p = pts[i]!;
      const x = cx + p.x;
      const y = cy + p.y;
      // Seats fan outward, so the department's own label reads inward,
      // toward the core, where nothing else sits.
      depts.push(node(`dept:${d.key}`, "dept", x, y, INWARD[sideFor(p.x, p.y)],
                      deptSize(d), d.key));
      // Seats fan outward from the department, away from the core.
      const list = [...d.agents, ...d.jobs];
      const base = Math.atan2(p.y, p.x);
      const r = 62 + list.length * 2.5;
      const step = Math.min(0.42, 2.6 / Math.max(1, list.length));
      fan(list.length, base, step).forEach((t, j) => {
        const sx = x + Math.cos(t) * r;
        const sy = y + Math.sin(t) * r * 0.8;
        seats.push(node(list[j]!.id, "seat", sx, sy, sideFor(sx - cx, sy - cy),
                        seatSize(list[j]!.ran_7d), d.key, list[j]!));
      });
    });
    if (coreDept) {
      const list = [...coreDept.agents, ...coreDept.jobs];
      // The core's own seats sit just below it, clear of the label above.
      fan(list.length, Math.PI / 2, 0.9).forEach((t, j) => {
        const sx = cx + Math.cos(t) * 78;
        const sy = cy + Math.sin(t) * 64;
        seats.push(node(list[j]!.id, "seat", sx, sy, sx < cx ? "left" : "right",
                        seatSize(list[j]!.ran_7d), coreDept.key, list[j]!));
      });
    }
  } else {
    const c = BRAIN_COMPACT;
    you = node("you", "you", frame.cx, c.youY, "right", 16);
    core = node(coreDept ? `dept:${coreDept.key}` : "core", "core", frame.cx, c.coreY,
                "right", 20, coreDept?.key ?? null);
    if (coreDept) {
      const list = [...coreDept.agents, ...coreDept.jobs];
      list.forEach((s, j) => {
        const sx = frame.cx - 70 - j * c.seatGap;
        seats.push(node(s.id, "seat", sx, c.coreY, "bottom", seatSize(s.ran_7d), coreDept.key, s));
      });
    }
    let top = c.firstRow;
    for (let i = 0; i < ring.length; i += c.cols) {
      const row = ring.slice(i, i + c.cols);
      const tallest = Math.max(...row.map((d) => Math.ceil((d.agents.length + d.jobs.length) / c.seatCols)));
      row.forEach((d, k) => {
        const x = row.length === 1 ? frame.cx : c.colX[k]!;
        const y = top;
        depts.push(node(`dept:${d.key}`, "dept", x, y, "top", Math.min(20, deptSize(d)), d.key));
        [...d.agents, ...d.jobs].forEach((s, j) => {
          const col = j % c.seatCols;
          const r = Math.floor(j / c.seatCols);
          const sx = x + (col - (c.seatCols - 1) / 2) * c.seatGap;
          const sy = y + c.seatTop + r * c.seatGap;
          seats.push(node(s.id, "seat", sx, sy, "bottom", seatSize(s.ran_7d), d.key, s));
        });
      });
      top += c.seatTop + tallest * c.seatGap + c.rowPad;
    }
  }

  const links: BrainLink[] = [];
  for (const d of depts) {
    links.push({ id: `spoke:${d.dept}`, kind: "spoke", dept: d.dept!,
                 d: `M${d.x},${d.y} L${core.x},${core.y}` });
  }
  for (const s of seats) {
    const owner = s.dept && coreDept && s.dept === coreDept.key
      ? core : depts.find((d) => d.dept === s.dept);
    if (!owner) continue;
    links.push({ id: `seat:${s.id}`, kind: "seat", dept: s.dept!,
                 d: `M${owner.x},${owner.y} L${s.x},${s.y}` });
  }
  // YOU -> every department with a Tier 2 action: a curve bowed away from
  // the core so it never crosses the spokes' hub.
  for (const d of [core, ...depts]) {
    if (!d.dept || !signoff.has(d.dept)) continue;
    const mx = (you.x + d.x) / 2;
    const my = (you.y + d.y) / 2;
    const bow = variant === "wide" ? (d.x < frame.cx ? -90 : d.x > frame.cx ? 90 : 0) : (d.x < frame.cx ? -60 : 60);
    links.push({ id: `signoff:${d.dept}`, kind: "signoff", dept: d.dept,
                 d: `M${you.x},${you.y} Q${round(mx + bow)},${round(my)} ${d.x},${d.y}` });
  }

  const byId = new Map<string, BrainNode>();
  for (const x of [you, core, ...depts, ...seats]) byId.set(x.id, x);
  return { frame, variant, you, core, depts, seats, links, byId };
}

function compactFrame(ring: BrainDepartment[], _core: BrainDepartment | null): BrainFrame {
  const c = BRAIN_COMPACT;
  let top = c.firstRow;
  for (let i = 0; i < ring.length; i += c.cols) {
    const row = ring.slice(i, i + c.cols);
    const tallest = Math.max(1, ...row.map((d) => Math.ceil((d.agents.length + d.jobs.length) / c.seatCols)));
    top += c.seatTop + tallest * c.seatGap + c.rowPad;
  }
  const h = top - c.rowPad + 40;
  return { w: c.w, h, cx: c.w / 2, cy: c.coreY, rx: 0, ry: 0 };
}
