/**
 * Fleet's cards and the deep-dive check list, as pure functions.
 *
 * Two ways the page used to keep an OK look it had no right to: the deep
 * dive drew each check's raw last history entry, with no staleness rule, so
 * a stale OK rendered cyan; and after a failed refresh of the roster every
 * card kept its old AVAILABLE/WORKING. Both now read as UNKNOWN -- grey,
 * dashed, the word visible (invariant 7).
 */
import type { AgentSummary } from "../api";
import { normaliseHealth } from "../api";

const WORKING_WINDOW_MS = 2 * 60 * 1000;

/** WORKING means a run inside the last two minutes. An agent whose provider
 *  chain does not resolve is UNAVAILABLE. A roster that could not be read
 *  this time is UNKNOWN: its old statuses are not statuses of now. */
export type Status = "AVAILABLE" | "UNAVAILABLE" | "WORKING" | "UNKNOWN";

export function status(agent: AgentSummary, readFailed = false,
                       now: number = Date.now()): Status {
  if (readFailed) return "UNKNOWN";
  if (!agent.available) return "UNAVAILABLE";
  if (agent.last_run_at) {
    const age = now - new Date(agent.last_run_at).getTime();
    if (age >= 0 && age < WORKING_WINDOW_MS) return "WORKING";
  }
  return "AVAILABLE";
}

/** UNAVAILABLE and UNKNOWN reuse the grey/dashed/unlit vocabulary UNKNOWN
 *  uses everywhere -- an absence, never a dimmer shade of ready. */
export function stateAttr(s: Status): "available" | "working" | "unknown" {
  if (s === "UNAVAILABLE" || s === "UNKNOWN") return "unknown";
  if (s === "WORKING") return "working";
  return "available";
}

/** The fleet card's MODEL text, before `rel()` turns `model_at` into a
 *  relative time -- three states, never collapsed into one "—":
 *    "unrun"    never ran at all (`model_at` is null)
 *    "silent"   ran, but nothing answered (e.g. `LocalModelUnavailable` --
 *               `model_at` is set, `model` is null: M2, Task 53 review fix
 *               round 1. `run_agent`/`Agent.run()` write empty model and
 *               provider strings for that run, which `_last_model()` on the
 *               server renders as `model: null` -- indistinguishable from
 *               "never ran" without `model_at` to tell them apart.)
 *    "answered" ran and something answered: `model` itself */
export type ModelState = "unrun" | "silent" | "answered";

export function modelState(
  agent: Pick<AgentSummary, "model" | "model_at">,
): ModelState {
  if (agent.model) return "answered";
  if (agent.model_at) return "silent";
  return "unrun";
}

export interface CheckReading {
  health: "ok" | "degraded" | "failing" | "unknown";
  at: string | null;
  stale: boolean;
}

/** A check as it stands now: the server's `current` (its staleness rule
 *  already applied -- a stale OK arrives as unknown with `stale: true`),
 *  never the raw last history entry. */
export function checkReading(check: {
  current?: { health: string; stale: boolean; at: string | null } | null;
}): CheckReading {
  const now = check.current;
  if (!now) return { health: "unknown", at: null, stale: false };
  return { health: normaliseHealth(now.health), at: now.at, stale: Boolean(now.stale) };
}
