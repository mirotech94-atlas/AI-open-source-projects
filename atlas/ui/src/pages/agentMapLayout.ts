/**
 * The Agents map's model, as pure functions (Task 69's redesign -- a
 * concierge card on top, a Businesses lane and a Functions lane below it,
 * both a responsive grid of cards, replacing the old 16-node ring).
 *
 * Two things this file decides, and nothing about pixels or SVG:
 *   agentMapModel  -- which lane each roster agent belongs in. Every agent
 *     is placed exactly once; a `group` this page does not recognise lands
 *     in Functions rather than being silently dropped.
 *   connectionsFor -- which OTHER agents light up when one card is
 *     selected: its handoffs (either direction) and anyone sharing a
 *     memory_scope namespace. `null` (nothing selected) draws nothing --
 *     the map has no edges until something is selected.
 */
import type { AgentSummary } from "../api";

const WORKING_WINDOW_MS = 2 * 60 * 1000;

/** The status dot on a card: not-ready is its own look (never a shade of
 * ready -- invariant 7's "never shares a treatment with OK" applies here
 * just as much as to a health check), and a failed roster read is
 * "unknown" regardless of what the agent's own last row said. */
export type CardDot = "ready" | "working" | "not-ready" | "unknown";

export function dotFor(
  agent: Pick<AgentSummary, "ready" | "last_run_at">,
  readFailed = false,
  now: number = Date.now(),
): CardDot {
  if (readFailed) return "unknown";
  if (!agent.ready) return "not-ready";
  if (agent.last_run_at) {
    const age = now - new Date(agent.last_run_at).getTime();
    if (age >= 0 && age < WORKING_WINDOW_MS) return "working";
  }
  return "ready";
}

export interface AgentMapModel {
  lead: AgentSummary | null;
  businesses: AgentSummary[];
  functions: AgentSummary[];
}

/** One lane per roster agent, concierge (whichever agent's `group` is
 * "lead") on top. Deterministic: every lane is sorted by name, so the same
 * roster always lays out the same way regardless of array order. */
export function agentMapModel(agents: AgentSummary[]): AgentMapModel {
  const sorted = [...agents].sort((a, b) => a.name.localeCompare(b.name));
  const lead = sorted.find((a) => a.group === "lead") ?? null;
  const businesses = sorted.filter((a) => a !== lead && a.group === "businesses");
  const functions = sorted.filter((a) => a !== lead && a.group !== "businesses");
  return { lead, businesses, functions };
}

export interface Connections {
  handoff: Set<string>;
  memory: Set<string>;
}

const EMPTY_CONNECTIONS: Connections = { handoff: new Set(), memory: new Set() };

/** Who lights up when `name` is selected. `null`, or a name the roster does
 * not (or no longer) hold, draws nothing -- the same "never point to
 * nowhere" rule the old ring's edges kept. */
export function connectionsFor(
  name: string | null, agents: AgentSummary[],
): Connections {
  if (name === null) return EMPTY_CONNECTIONS;
  const known = new Set(agents.map((a) => a.name));
  const self = agents.find((a) => a.name === name);
  if (!self) return EMPTY_CONNECTIONS;

  const handoff = new Set<string>();
  for (const target of self.handoff) {
    if (target !== name && known.has(target)) handoff.add(target);
  }
  for (const a of agents) {
    if (a.name !== name && a.handoff.includes(name)) handoff.add(a.name);
  }

  const memory = new Set<string>();
  for (const a of agents) {
    if (a.name === name) continue;
    if (a.memory_scope.some((ns) => self.memory_scope.includes(ns))) {
      memory.add(a.name);
    }
  }

  return { handoff, memory };
}

// ---------------------------------------------------------------- radial map
// The Agency tab's map (design v2): ATLAS in the centre, every roster agent on
// one ring, curved links. Added beside the lane model above, which the Home
// and room maps still use. Pure: no pixels beyond the frame, no SVG.

export const RADIAL_FRAME = { w: 760, h: 470, cx: 380, cy: 232, rx: 292, ry: 148 } as const;

export interface RadialNode { name: string; x: number; y: number }
export interface RadialLink {
  id: string;
  kind: "spoke" | "handoff" | "memory";
  from: string;
  to: string;
  d: string;
}
export interface RadialLayout {
  frame: typeof RADIAL_FRAME;
  nodes: RadialNode[];
  links: RadialLink[];
}

const r1 = (n: number): number => Math.round(n * 10) / 10;

/** Lead first (top of the ring), then the rest by name, evenly spaced. */
export function radialLayout(agents: AgentSummary[]): RadialLayout {
  const f = RADIAL_FRAME;
  const sorted = [...agents].sort((a, b) => a.name.localeCompare(b.name));
  const lead = sorted.find((a) => a.group === "lead") ?? null;
  const ordered = lead ? [lead, ...sorted.filter((a) => a !== lead)] : sorted;
  const n = ordered.length;
  const nodes: RadialNode[] = ordered.map((a, i) => {
    const t = (-90 + (360 * i) / n) * (Math.PI / 180);
    return { name: a.name, x: r1(f.cx + f.rx * Math.cos(t)), y: r1(f.cy + f.ry * Math.sin(t)) };
  });
  const at = new Map(nodes.map((p) => [p.name, p]));
  const links: RadialLink[] = [];

  // Spokes: a gentle curve from the core to each agent.
  for (const p of nodes) {
    const mx = (f.cx + p.x) / 2;
    const my = (f.cy + p.y) / 2;
    const dx = p.x - f.cx;
    const dy = p.y - f.cy;
    const k = 0.12;
    const cx = r1(mx - dy * k);
    const cy = r1(my + dx * k);
    links.push({ id: `s-${p.name}`, kind: "spoke", from: "atlas", to: p.name,
                 d: `M ${f.cx} ${f.cy} Q ${cx} ${cy} ${p.x} ${p.y}` });
  }

  // Handoffs the roster declares, between roster agents only, one per pair;
  // memory sharing only where no handoff already joins the pair.
  const curve = (a: RadialNode, b: RadialNode): string => {
    const mx = (a.x + b.x) / 2;
    const my = (a.y + b.y) / 2;
    return `M ${a.x} ${a.y} Q ${r1(mx + (f.cx - mx) * 0.6)} ${r1(my + (f.cy - my) * 0.6)} ${b.x} ${b.y}`;
  };
  const seen = new Set<string>();
  const pair = (a: string, b: string): string => (a < b ? `${a}|${b}` : `${b}|${a}`);
  for (const a of ordered) {
    for (const h of new Set(a.handoff)) {
      const pa = at.get(a.name);
      const pb = at.get(h);
      if (!pa || !pb || h === a.name || seen.has(pair(a.name, h))) continue;
      seen.add(pair(a.name, h));
      links.push({ id: `h-${a.name}-${h}`, kind: "handoff", from: a.name, to: h, d: curve(pa, pb) });
    }
  }
  for (let i = 0; i < ordered.length; i++) {
    for (let j = i + 1; j < ordered.length; j++) {
      const a = ordered[i]!;
      const b = ordered[j]!;
      if (seen.has(pair(a.name, b.name))) continue;
      if (!a.memory_scope.some((ns) => b.memory_scope.includes(ns))) continue;
      const pa = at.get(a.name)!;
      const pb = at.get(b.name)!;
      links.push({ id: `m-${a.name}-${b.name}`, kind: "memory", from: a.name, to: b.name,
                   d: curve(pa, pb) });
    }
  }
  return { frame: f, nodes, links };
}
