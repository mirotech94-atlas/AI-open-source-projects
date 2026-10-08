/**
 * The Brain's words and numbers, as pure functions (no DOM, no clock), so
 * the rule "unknown is never 0% and never 100%" is tested once and every
 * surface -- the strip, a department card, the map -- says it the same way.
 */
import type {
  Brain, BrainDepartment, BrainLayer, BrainRuns, BrainSeat, SopStep,
} from "../api";

export const LAYER_LABEL: Record<BrainLayer, string> = {
  automation: "Automation",
  agent: "AI agent",
  agentic: "Agentic system",
};

export const LAYER_SHORT: Record<BrainLayer, string> = {
  automation: "AUTO",
  agent: "AGENT",
  agentic: "SYSTEM",
};

/** "75%" from a measured share, "unknown" for everything else: a failed
 *  read (null), no executions in the window, or a share that is not a
 *  finite number. Never a 0% standing in for "nothing to count". */
export function pctText(runs: BrainRuns | null | undefined): string {
  if (!runs || runs.executions <= 0 || runs.pct === null || !Number.isFinite(runs.pct)) {
    return "unknown";
  }
  const whole = Math.round(runs.pct);
  return `${Math.abs(runs.pct - whole) < 0.05 ? whole : runs.pct.toFixed(1)}%`;
}

export function isUnknown(runs: BrainRuns | null | undefined): boolean {
  return pctText(runs) === "unknown";
}

/** Why a share reads unknown, in one line. */
export function unknownWhy(runs: BrainRuns | null | undefined, days: number,
                           watchOnly: boolean): string {
  if (runs === null || runs === undefined) return "the action log could not be read";
  if (watchOnly) return "watch-only: nothing is allowed to run";
  return `no action ran in the last ${days} days`;
}

/** The share's working, for a tooltip: "3 on their own, 1 approved by you". */
export function runsDetail(runs: BrainRuns | null | undefined): string {
  if (!runs) return "not read";
  const base = `${runs.auto} ran on their own, ${runs.approved} approved by you`;
  return runs.excluded > 0 ? `${base}; ${runs.excluded} without a recorded tier left out` : base;
}

export function countText(n: number | null | undefined): string {
  return n === null || n === undefined ? "unknown" : String(n);
}

/** "3/5 ran" for agents; jobs with no per-run record are said, not counted. */
export function seatsLine(d: BrainDepartment): string {
  const s = d.seats;
  const agents = s.agents === 0 ? "no agents"
    : `${s.agents_ran === null ? "?" : s.agents_ran}/${s.agents} agent${s.agents === 1 ? "" : "s"} ran`;
  const unrec = s.jobs - s.jobs_recorded;
  let jobs = s.jobs === 0 ? "no jobs"
    : s.jobs_recorded === 0 ? `${s.jobs} job${s.jobs === 1 ? "" : "s"}, none recorded per run`
      : `${s.jobs_ran === null ? "?" : s.jobs_ran}/${s.jobs_recorded} recorded job${s.jobs_recorded === 1 ? "" : "s"} ran`;
  if (s.jobs_recorded > 0 && unrec > 0) jobs += ` (+${unrec} not recorded)`;
  return `${agents} · ${jobs}`;
}

export type RanState = "ran" | "idle" | "unknown";

/** A seat's dot: ran in the window, did not, or no record to say. */
export function ranState(seat: Pick<BrainSeat, "ran_7d">): RanState {
  if (seat.ran_7d === null || seat.ran_7d === undefined) return "unknown";
  return seat.ran_7d > 0 ? "ran" : "idle";
}

export function ranText(seat: Pick<BrainSeat, "ran_7d">, days: number): string {
  const s = ranState(seat);
  if (s === "unknown") return "not recorded per run";
  return `${seat.ran_7d} run${seat.ran_7d === 1 ? "" : "s"} in ${days}d`;
}

export function seatsOf(d: BrainDepartment): BrainSeat[] {
  return [...d.agents, ...d.jobs];
}

/** The SOP for a seat or action id, or null when the payload has none. */
export function sopFor(brain: Brain | null, id: string | null): SopStep[] | null {
  if (!brain || !id) return null;
  return brain.sops[id] ?? null;
}

/** Find what an id names, for the SOP panel's heading. */
export function describe(brain: Brain, id: string): { title: string; sub: string; layer: BrainLayer | null; dept: BrainDepartment | null } {
  for (const d of brain.departments) {
    for (const s of seatsOf(d)) {
      if (s.id === id) {
        const title = s.kind === "agent" ? `${s.label} · ${s.title}` : `${s.label} (job)`;
        return { title, sub: d.name, layer: s.layer, dept: d };
      }
    }
    for (const a of d.actions) {
      if (a.id === id) return { title: a.name, sub: `${d.name} · Tier ${a.tier}`, layer: null, dept: d };
    }
  }
  return { title: id.replace(/^\w+:/, ""), sub: "", layer: null, dept: null };
}

export const STEP_WORD: Record<SopStep["kind"], string> = {
  trigger: "Trigger",
  reads: "Reads",
  decides: "Decides",
  acts: "Acts",
  signoff: "Your sign-off",
  result: "Result goes to",
};

/** Departments around the core, core first. */
export function ordered(brain: Brain): { core: BrainDepartment | null; ring: BrainDepartment[] } {
  const core = brain.departments.find((d) => d.core) ?? null;
  return { core, ring: brain.departments.filter((d) => !d.core) };
}

/** Departments that hold at least one Tier 2 action: YOU are wired to them. */
export function signoffDepartments(brain: Brain): string[] {
  return brain.departments.filter((d) => d.tier2_actions > 0).map((d) => d.key);
}

/** The read-back line for the strip: "12 verified · 1 contradicted · 3 unsure". */
export function verifyLine(v: Record<string, number> | null, on: boolean): string {
  if (v === null) return "unknown (not read)";
  const verified = v.verified ?? 0;
  const contradicted = v.contradicted ?? 0;
  const unsure = Object.entries(v)
    .filter(([k]) => k !== "verified" && k !== "contradicted")
    .reduce((n, [, c]) => n + c, 0);
  if (verified + contradicted + unsure === 0) {
    return on ? "none in the window" : "read-back is off";
  }
  return `${verified} verified · ${contradicted} contradicted · ${unsure} unsure`;
}
