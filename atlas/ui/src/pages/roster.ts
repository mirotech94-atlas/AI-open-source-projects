/**
 * The agency's roster, as the server builds it (kernel/agents.py,
 * 2026-10-28): nine agents. `inbox` is the front door -- a message no
 * other agent owns lands there -- and is drawn as the lead. The map and the
 * Team page read the live roster (`GET /api/agents`); these names only say
 * which one leads and which retired names must never be drawn as an agent.
 */
export const FRONT_DOOR_AGENT = "inbox";

export const ROSTER_AGENTS = [
  "inbox", "sites", "content", "qa", "sales", "bizops", "seo", "research", "tutorco",
] as const;

export const RETIRED_AGENTS = [
  "agency", "analyst", "commerce", "concierge", "fiverr", "flycheap", "growth",
  "guardian", "attendance", "marketing", "markets", "mirotech", "ops", "reels",
  "scheduler", "school", "sentinel", "cloudhost",
] as const;

const RETIRED = new Set<string>(RETIRED_AGENTS);

/** True for a name that left the roster: an old row may still carry it. */
export function isRetiredAgent(name: string | null | undefined): boolean {
  return name ? RETIRED.has(name) : false;
}
