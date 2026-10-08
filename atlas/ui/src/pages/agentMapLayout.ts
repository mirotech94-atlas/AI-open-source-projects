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
