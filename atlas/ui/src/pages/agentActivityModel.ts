/**
 * The Agency map's activity panel, as pure functions: what each agent is
 * doing now, what it did yesterday, and its issues, from
 * `GET /api/agency/activity`. A null figure is UNKNOWN (grey, dashed, the
 * word), never 0 and never OK. No table text: names, counts and times only.
 */
import type { ActivityIssue, AgentActivity } from "../api";
import { hhmm } from "./homeModel";

/** How often the panel re-reads, ms. */
export const ACTIVITY_REFRESH_MS = 30_000;

export type Line = { text: string; health: "ok" | "degraded" | "failing" | "unknown" | "plain" };

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? "" : "s"}`;

/** "now": what it has in hand, what waits on the owner, its last run. */
export function nowLines(a: AgentActivity): Line[] {
  const n = a.now;
  const out: Line[] = [];
  if (n.in_flight === null && n.waiting === null) {
    out.push({ text: `work board not read (${n.unread.join(", ") || "no reading"})`,
               health: "unknown" });
  } else {
    if (n.in_flight !== null) {
      out.push({ text: n.in_flight === 0 ? "nothing in flight" : `${plural(n.in_flight, "item")} in flight`,
                 health: "plain" });
    }
    if (n.waiting !== null) {
      out.push({ text: n.waiting === 0 ? "no card waiting"
        : `${plural(n.waiting, "card")} waiting: ${n.cards.map((c) => c.action).join(", ")}`,
                 health: n.waiting > 0 ? "degraded" : "plain" });
    }
  }
  for (const job of n.running_jobs) out.push({ text: `running ${job}`, health: "plain" });
  if (!n.last_run_known) out.push({ text: "last run not read", health: "unknown" });
  else if (n.last_run === null) out.push({ text: "has never run", health: "plain" });
  else {
    out.push({ text: `last run ${hhmm(n.last_run.at)}${n.last_run.failed ? ", failed" : ""}`,
               health: n.last_run.failed ? "failing" : "plain" });
  }
  return out;
}

/** "yesterday": runs and its own actions over the previous New York day. */
export function yesterdayLines(a: AgentActivity): Line[] {
  const y = a.yesterday;
  const out: Line[] = [];
  if (y.runs === null) out.push({ text: "runs not read", health: "unknown" });
  else {
    const failed = y.failed_runs ? `, ${y.failed_runs} failed` : "";
    out.push({ text: y.runs === 0 ? "no run" : `${plural(y.runs, "run")}${failed}`
      + (y.last_run_at ? `, last ${hhmm(y.last_run_at)}` : ""),
               health: y.failed_runs ? "failing" : "plain" });
  }
  if (y.actions_ok === null || y.actions_failed === null) {
    out.push({ text: "actions not read", health: "unknown" });
  } else {
    const total = y.actions_ok + y.actions_failed;
    out.push({ text: total === 0 ? "no action ran"
      : `${plural(total, "action")} ran` + (y.actions_failed ? `, ${y.actions_failed} failed` : ""),
               health: y.actions_failed ? "failing" : "plain" });
    for (const act of y.last_actions) {
      out.push({ text: `${act.action} ${hhmm(act.at)}`
        + (act.ok === false ? " failed" : act.ok === null ? " (result unread)" : ""),
                 health: act.ok === false ? "failing" : act.ok === null ? "unknown" : "plain" });
    }
  }
  if (y.cards_expired !== null && y.cards_expired > 0) {
    out.push({ text: `${plural(y.cards_expired, "card")} expired unanswered`, health: "degraded" });
  }
  return out;
}

/** One issue in words. */
export function issueText(i: ActivityIssue): string {
  const at = i.at ? ` (${hhmm(i.at)})` : "";
  switch (i.kind) {
    case "runs_failed": return `${plural(i.count ?? 0, "run")} failed in 24 h${at}`;
    case "check_failing": return `${i.module}.${i.check} failing${at}`;
    case "check_degraded": return `${i.module}.${i.check} degraded${at}`;
    case "check_unknown":
      return `${i.module}.${i.check} ${i.stale ? "stale" : "not measured"}${at}`;
    case "job_failed": return `job ${i.job} failed${at}`;
    case "job_stalled": return `job ${i.job} stalled, running since ${hhmm(i.at)}`;
    case "cards_expired": return `${plural(i.count ?? 0, "card")} ${i.action} expired${at}`;
  }
}

export function issueHealth(i: ActivityIssue): Line["health"] {
  if (i.kind === "check_unknown") return "unknown";
  if (i.kind === "check_degraded" || i.kind === "cards_expired" || i.kind === "job_stalled") {
    return "degraded";
  }
  return "failing";
}

export function issueLines(a: AgentActivity): Line[] {
  const out: Line[] = a.issues.items.map((i) => ({ text: issueText(i), health: issueHealth(i) }));
  for (const what of a.issues.unread) out.push({ text: `${what} not read`, health: "unknown" });
  if (out.length === 0) out.push({ text: "no issue found", health: "plain" });
  return out;
}

/** The agent's overall lamp: failing beats degraded beats unknown beats ok. */
export function agentHealth(a: AgentActivity): Line["health"] {
  const all = [...nowLines(a), ...yesterdayLines(a), ...issueLines(a)].map((l) => l.health);
  for (const h of ["failing", "degraded", "unknown"] as const) if (all.includes(h)) return h;
  return "ok";
}

export type Meter =
  | { state: "unknown" }
  | { state: "read"; ok: number; failed: number; total: number; okPct: number; failedPct: number };

/** The card's progress bar: yesterday's own actions, ok against failed.
 *  Both figures must have been read; otherwise UNKNOWN (never 0, never full). */
export function agentMeter(a: AgentActivity): Meter {
  const { actions_ok: ok, actions_failed: failed } = a.yesterday;
  if (ok === null || failed === null) return { state: "unknown" };
  const total = ok + failed;
  const pct = (n: number): number => (total === 0 ? 0 : Math.round((n / total) * 100));
  return { state: "read", ok, failed, total, okPct: pct(ok), failedPct: pct(failed) };
}
