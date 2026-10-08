/**
 * The agency map's geometry, as pure functions: no DOM, no clock of its own
 * (`now` is always passed in), so the same roster at the same instant lays
 * out and lights up the same way every time -- and the tests can say so.
 *
 * The picture: ATLAS is a small core at the centre. The concierge sits at
 * the top of the inner ring with the businesses; the role agents (the
 * functions every business hands work to) sit on the outer ring, offset by
 * half a step so no neuron hides behind another. Each neuron is wired to
 * the core by a faint spoke. Synapses are drawn only for relationships the
 * roster actually declares:
 *   handoff -- one agent names another in `handoff` (either direction);
 *   memory  -- two agents share a `memory_scope` namespace.
 * A pair that has both is drawn once, as a handoff. Nothing is inferred.
 */
import type { AgentSummary } from "../api";
import { agentMapModel } from "../pages/agentMapLayout";

export type Variant = "wide" | "compact";
export type Side = "left" | "right" | "top" | "bottom";
export type Lane = "lead" | "businesses" | "functions";

/** A run this recent sends a signal down the agent's spoke to the core. */
export const PULSE_WINDOW_MS = 10 * 60 * 1000;

/** Below this container width the map switches to the compact frame. */
export const COMPACT_BELOW_PX = 900;

export interface Frame {
  w: number; h: number; cx: number; cy: number;
  inner: { rx: number; ry: number };
  outer: { rx: number; ry: number };
}

/** The wide frame: two elliptical rings around the core. Wider than tall,
 *  with room at both sides for labels that read along the line. */
export const WIDE_FRAME: Frame = {
  w: 1320, h: 720, cx: 660, cy: 360,
  inner: { rx: 235, ry: 150 }, outer: { rx: 420, ry: 285 },
};

/** The compact (phone) frame is a different shape, not the wide one
 *  scaled: labels are HTML and keep their size, so 21 names cannot sit on
 *  a 343px ring. Instead the net is drawn in layers, top to bottom -- the
 *  core, the concierge, the businesses, the functions -- four neurons to a
 *  row, each name under its neuron. Its height follows the row count. */
export const COMPACT = { w: 400, cols: 4, colW: 96, top: 44, leadGap: 84, bandGap: 104, rowH: 84, bottom: 56 };

export function variantFor(widthPx: number): Variant {
  return widthPx > 0 && widthPx < COMPACT_BELOW_PX ? "compact" : "wide";
}

export interface Neuron {
  agent: AgentSummary;
  lane: Lane;
  /** Position in the frame's own units (the SVG viewBox). */
  x: number; y: number;
  /** The same position as a percentage of the frame, for the HTML node. */
  px: number; py: number;
  /** Which side of the neuron its label sits on: always away from the core. */
  side: Side;
  /** Side of the square neuron in CSS px -- by runs in the last 24 hours. */
  size: number;
}

export type SynapseKind = "handoff" | "memory";

export interface Synapse {
  id: string;
  a: string;
  b: string;
  kind: SynapseKind;
  d: string;
}

export interface Spoke {
  name: string;
  /** From the neuron to the core, so a signal runs inward. */
  d: string;
}

export interface NeuralLayout {
  frame: Frame;
  /** Compact only: where each lane's band starts, as a % of the height
   *  (the lane title sits there). Null in the wide frame, whose rings are
   *  their own key. */
  bands: { businesses: number; functions: number } | null;
  variant: Variant;
  lead: Neuron | null;
  businesses: Neuron[];
  functions: Neuron[];
  byName: Map<string, Neuron>;
  synapses: Synapse[];
  spokes: Spoke[];
}

const round = (n: number): number => Math.round(n * 10) / 10;

/** A neuron's side in px: 8 for an agent that has not run in the last day,
 *  growing with the square root of its runs, capped at 22 -- so 31 runs
 *  never dwarfs 3 runs into a speck. */
export function neuronSize(runs24h: number): number {
  const runs = Number.isFinite(runs24h) && runs24h > 0 ? runs24h : 0;
  return Math.min(22, Math.round(8 + Math.sqrt(runs) * 2.5));
}

/** Label side from the direction away from the core. Mostly left/right
 *  (labels read along the line); top/bottom only near the poles. */
export function sideFor(dx: number, dy: number): Side {
  const len = Math.hypot(dx, dy) || 1;
  const cos = dx / len;
  if (Math.abs(cos) >= 0.4) return cos > 0 ? "right" : "left";
  return dy < 0 ? "top" : "bottom";
}

/** `n` points evenly spaced by angle around an ellipse, the first at
 *  `start` radians (-π/2 is 12 o'clock), clockwise on screen. */
export function ringPoints(
  n: number, rx: number, ry: number, start: number,
): { x: number; y: number }[] {
  const out: { x: number; y: number }[] = [];
  for (let i = 0; i < n; i++) {
    const t = start + (2 * Math.PI * i) / n;
    out.push({ x: Math.cos(t) * rx, y: Math.sin(t) * ry });
  }
  return out;
}

/** Every declared relationship between two roster agents, once per pair.
 *  Handoff beats memory for the same pair; a handoff to an agent the
 *  roster does not hold, or to itself, is dropped rather than drawn to
 *  nowhere. Sorted, so the SVG order never depends on the roster's. */
export function synapsePairs(
  agents: Pick<AgentSummary, "name" | "handoff" | "memory_scope">[],
): { a: string; b: string; kind: SynapseKind }[] {
  const known = new Set(agents.map((x) => x.name));
  const pairs = new Map<string, { a: string; b: string; kind: SynapseKind }>();
  const key = (p: string, q: string): [string, string, string] => {
    const [a, b] = p < q ? [p, q] : [q, p];
    return [a, b, `${a}|${b}`];
  };
  for (const x of agents) {
    for (const target of x.handoff) {
      if (target === x.name || !known.has(target)) continue;
      const [a, b, k] = key(x.name, target);
      pairs.set(k, { a, b, kind: "handoff" });
    }
  }
  for (let i = 0; i < agents.length; i++) {
    for (let j = i + 1; j < agents.length; j++) {
      const p = agents[i]!;
      const q = agents[j]!;
      if (p.name === q.name) continue;
      if (!p.memory_scope.some((ns) => q.memory_scope.includes(ns))) continue;
      const [a, b, k] = key(p.name, q.name);
      if (!pairs.has(k)) pairs.set(k, { a, b, kind: "memory" });
    }
  }
  return [...pairs.values()].sort((m, n) =>
    m.a === n.a ? m.b.localeCompare(n.b) : m.a.localeCompare(n.a));
}

/** A synapse bows toward the core -- the chord between two neurons pulled
 *  halfway to the centre -- so the web reads as a brain, not a mesh, and
 *  never lies on top of the straight spokes. */
/** How close a synapse's bend may come to the core, in frame units. */
const CORE_CLEARANCE = 60;

function chord(p: Neuron, q: Neuron, f: Frame, variant: Variant): string {
  if (variant === "compact") {
    // Layers: an S-curve between rows; within a row, a shallow arc above
    // it so the synapse never runs through the neurons between.
    if (Math.abs(p.y - q.y) < 1) {
      const lift = 18 + Math.abs(p.x - q.x) * 0.12;
      return `M${round(p.x)},${round(p.y)} Q${round((p.x + q.x) / 2)},${round(p.y - lift)} ${round(q.x)},${round(q.y)}`;
    }
    const my = (p.y + q.y) / 2;
    return `M${round(p.x)},${round(p.y)} C${round(p.x)},${round(my)} ${round(q.x)},${round(my)} ${round(q.x)},${round(q.y)}`;
  }
  const mx = (p.x + q.x) / 2;
  const my = (p.y + q.y) / 2;
  let cx = mx + (f.cx - mx) * 0.55;
  let cy = my + (f.cy - my) * 0.55;
  // Two neurons on opposite sides would chord straight through the core;
  // bend that synapse around it instead, to the side it already leans.
  if (Math.hypot(cx - f.cx, cy - f.cy) < CORE_CLEARANCE) {
    const dx = q.x - p.x;
    const dy = q.y - p.y;
    const len = Math.hypot(dx, dy) || 1;
    let nx = -dy / len;
    let ny = dx / len;
    if (nx * (mx - f.cx) + ny * (my - f.cy) < 0) { nx = -nx; ny = -ny; }
    cx = f.cx + nx * CORE_CLEARANCE * 1.6;
    cy = f.cy + ny * CORE_CLEARANCE * 1.6;
  }
  return `M${round(p.x)},${round(p.y)} Q${round(cx)},${round(cy)} ${round(q.x)},${round(q.y)}`;
}

export function neuralLayout(agents: AgentSummary[], variant: Variant = "wide"): NeuralLayout {
  const model = agentMapModel(agents);
  const f: Frame = variant === "wide" ? WIDE_FRAME : compactFrame(model);

  const place = (a: AgentSummary, lane: Lane, x: number, y: number, side?: Side): Neuron => ({
    agent: a, lane,
    x: f.cx + x, y: f.cy + y,
    px: round(((f.cx + x) / f.w) * 100),
    py: round(((f.cy + y) / f.h) * 100),
    side: side ?? sideFor(x, y),
    size: neuronSize(a.runs_24h),
  });

  let innerNeurons: Neuron[];
  let functions: Neuron[];
  let bands: NeuralLayout["bands"] = null;
  if (variant === "compact") {
    // Rows of up to four, centred, top to bottom.
    const rows = (list: AgentSummary[], lane: Lane, y0: number): Neuron[] =>
      list.map((a, i) => {
        const row = Math.floor(i / COMPACT.cols);
        const inRow = Math.min(COMPACT.cols, list.length - row * COMPACT.cols);
        const col = i % COMPACT.cols;
        const x = (col - (inRow - 1) / 2) * COMPACT.colW;
        return place(a, lane, x, y0 + row * COMPACT.rowH - f.cy, "bottom");
      });
    const leadY = COMPACT.top + COMPACT.leadGap;
    const bizY = leadY + COMPACT.bandGap;
    const bizRows = Math.max(1, Math.ceil(model.businesses.length / COMPACT.cols));
    const fnY = bizY + (bizRows - 1) * COMPACT.rowH + COMPACT.bandGap;
    innerNeurons = [
      ...(model.lead ? [place(model.lead, "lead", 0, leadY - f.cy, "bottom")] : []),
      ...rows(model.businesses, "businesses", bizY),
    ];
    functions = rows(model.functions, "functions", fnY);
    bands = {
      businesses: round(((bizY - 44) / f.h) * 100),
      functions: round(((fnY - 44) / f.h) * 100),
    };
  } else {
    // Inner ring: the lead at 12 o'clock, then the businesses clockwise.
    const inner: { a: AgentSummary; lane: Lane }[] = [
      ...(model.lead ? [{ a: model.lead, lane: "lead" as const }] : []),
      ...model.businesses.map((a) => ({ a, lane: "businesses" as const })),
    ];
    const innerPts = ringPoints(inner.length, f.inner.rx, f.inner.ry, -Math.PI / 2);
    // Inner labels sit above or below their neuron, never sideways: sideways
    // they would run across the gap into the outer ring's labels.
    innerNeurons = inner.map(({ a, lane }, i) =>
      place(a, lane, innerPts[i]!.x, innerPts[i]!.y, innerPts[i]!.y < -1 || i === 0 ? "top" : "bottom"));
    // Outer ring: the functions, half a step off the top so a function
    // never sits straight behind the lead.
    const n = model.functions.length;
    const outerPts = ringPoints(n, f.outer.rx, f.outer.ry, -Math.PI / 2 + (n ? Math.PI / n : 0));
    // Outer labels always read outward, left or right, so two neurons near
    // a pole never stack their labels over each other.
    functions = model.functions.map((a, i) =>
      place(a, "functions", outerPts[i]!.x, outerPts[i]!.y, outerPts[i]!.x < 0 ? "left" : "right"));
  }

  const lead = innerNeurons.find((x) => x.lane === "lead") ?? null;
  const businesses = innerNeurons.filter((x) => x.lane === "businesses");
  const byName = new Map<string, Neuron>();
  for (const x of [...innerNeurons, ...functions]) byName.set(x.agent.name, x);

  const synapses: Synapse[] = synapsePairs(agents).flatMap(({ a, b, kind }) => {
    const p = byName.get(a);
    const q = byName.get(b);
    return p && q ? [{ id: `${kind}:${a}|${b}`, a, b, kind, d: chord(p, q, f, variant) }] : [];
  });

  const spokes: Spoke[] = [...byName.values()].map((x) => ({
    name: x.agent.name,
    d: `M${round(x.x)},${round(x.y)} L${f.cx},${f.cy}`,
  }));

  return { frame: f, variant, bands, lead, businesses, functions, byName, synapses, spokes };
}

/** The compact frame for this roster: the core near the top, its height
 *  grown to fit every row. `inner`/`outer` are unused (no rings). */
function compactFrame(model: { lead: unknown; businesses: unknown[]; functions: unknown[] }): Frame {
  const bizRows = Math.max(1, Math.ceil(model.businesses.length / COMPACT.cols));
  const fnRows = Math.max(1, Math.ceil(model.functions.length / COMPACT.cols));
  const lastY = COMPACT.top + COMPACT.leadGap + COMPACT.bandGap
    + (bizRows - 1) * COMPACT.rowH + COMPACT.bandGap + (fnRows - 1) * COMPACT.rowH;
  const h = lastY + COMPACT.bottom;
  return { w: COMPACT.w, h, cx: COMPACT.w / 2, cy: COMPACT.top,
           inner: { rx: 0, ry: 0 }, outer: { rx: 0, ry: 0 } };
}

/** Whether a signal runs down this agent's spoke: its last run is under
 *  ten minutes old. A failed roster read never pulses (unknown never
 *  glows), and neither does a run stamped in the future. Readiness does
 *  not gate it: a not-ready agent that did run a minute ago still ran,
 *  and hiding that would be the dishonest choice. */
export function isPulsing(
  agent: Pick<AgentSummary, "last_run_at">,
  readFailed: boolean,
  now: number,
): boolean {
  if (readFailed || !agent.last_run_at) return false;
  const age = now - new Date(agent.last_run_at).getTime();
  return Number.isFinite(age) && age >= 0 && age < PULSE_WINDOW_MS;
}

/** "Ava · Concierge" when the roster carries a persona, else the plain
 *  display name -- a persona is presentation only, never required. */
export function cardName(a: Pick<AgentSummary, "display" | "persona">): string {
  // A derived persona is named after the title ("Nightshift"); never say it twice.
  return a.persona && a.persona.name !== a.display ? `${a.persona.name} · ${a.display}` : a.display;
}

/** The short name a neuron carries where room is tight: the persona's
 *  first name, else the display name. */
export function shortName(a: Pick<AgentSummary, "display" | "persona">): string {
  return a.persona?.name ?? a.display;
}
