/**
 * The Agency tab's eight seats as pure functions: what each one reads, what
 * it has finished, what waits on an answer. No DOM, no fetch.
 *
 * - A seat's health comes from ITS checks only. Unknown (not read, module
 *   missing or set aside, a check stale or unknown) is never ok; trouble
 *   outranks unknown.
 * - "Done" is `execute.finish` and nothing else: refused, preview and
 *   proposal rows are not work done.
 * - Rows from retired lanes are dropped and counted.
 */
import { normaliseHealth, type ActivityRow, type AgentSummary, type Health, type ModuleTile } from "../api";
import type { DecisionRow } from "./alertsModel";
import { safeText, visible } from "./retired";
import { channelCheckLabel } from "./youtubeModel";
import { openCountFor, type RemediationTask } from "./remediationModel";

export type SeatKey =
  | "websites" | "personal" | "academy" | "sales" | "bizops" | "seo" | "research" | "qa";

export interface Seat {
  key: SeatKey;
  title: string;
  /** One line: what the seat is for. */
  role: string;
  brief: string;
  /** `module.name` or `module.prefix*`. */
  checks: readonly string[];
  actionPrefix: readonly string[];
  rosterAgents: readonly string[];
  slack: string;
}

export const SEATS: readonly Seat[] = [
  {
    key: "websites", title: "Client Websites", role: "Builds and ships client sites",
    brief: "Finds local businesses, mocks up and builds their websites, and drafts the outreach. "
      + "Nothing is sent or deployed without an answer in Slack.",
    checks: ["agency.*"], actionPrefix: ["agency."], rosterAgents: ["sites"],
    slack: "#agency-decisions",
  },
  {
    key: "personal", title: "Personal Ops", role: "Inbox and calendar",
    brief: "Watches the inbox and the calendar: drafts replies and nudges, applies labels, "
      + "and flags clashes and deadlines.",
    checks: ["mail.*", "calendar.*"], actionPrefix: ["mail.", "calendar."],
    rosterAgents: ["inbox"], slack: "#agency-alerts",
  },
  {
    key: "academy", title: "Content", role: "YouTube, the church channel and Reddit",
    brief: "Publishes the daily Atlas Academy tutorial, schedules the church channel's Shorts "
      + "and drafts the Reddit showcase posts. Every publish passes QA first.",
    checks: ["academy.*", "youtube.clips_*", "youtube.ecc_*", "church.*", "reddit.*"],
    actionPrefix: ["academy.", "youtube.", "church.", "reddit."],
    rosterAgents: ["content"], slack: "#agency-decisions",
  },
  {
    key: "sales", title: "Sales & Lead Gen", role: "Finds leads and drafts the pitch",
    brief: "Finds and scores local leads, drafts outreach and proposals at $500 for a new "
      + "client or $300 for a returning one. Every send is a card in Slack.",
    checks: ["sales.*"], actionPrefix: ["sales."], rosterAgents: ["sales"],
    slack: "#agency-decisions",
  },
  {
    key: "bizops", title: "Business Automation", role: "Invoices, reminders and automations",
    brief: "Drafts invoices and payment reminders in Stripe test mode and proposes small "
      + "automations. Nothing is charged or sent without an answer.",
    checks: ["bizops.*"], actionPrefix: ["bizops."], rosterAgents: ["bizops"],
    slack: "#agency-decisions",
  },
  {
    key: "seo", title: "SEO & Growth", role: "Audits and ranks client sites",
    brief: "Audits client sites, tracks their search ranks and drafts fixes. Draft only: a "
      + "change ships through the Client Websites seat after QA.",
    checks: ["seo.*"], actionPrefix: ["seo."], rosterAgents: ["seo"],
    slack: "#agency-decisions",
  },
  {
    key: "research", title: "Research & Intel", role: "Competitors and opportunities",
    brief: "Watches competitors and the market politely, writes sourced briefs and proposes "
      + "opportunities. Every figure carries its source and the time it was read.",
    checks: ["research.*"], actionPrefix: ["research."], rosterAgents: ["research"],
    slack: "#agency-decisions",
  },
  {
    key: "qa", title: "QA", role: "The gate before anything goes out",
    brief: "Checks every draft, site and publish before it can go out, scans our own sites "
      + "passively and raises a remediation task for each miss.",
    checks: ["qa.*"], actionPrefix: ["qa."], rosterAgents: ["qa"],
    slack: "#agency-alerts",
  },
];

const RANK: Record<Health, number> = { ok: 0, unknown: 1, degraded: 2, failing: 3 };

function split(pattern: string): { module: string; name: string } {
  const i = pattern.indexOf(".");
  return { module: pattern.slice(0, i), name: pattern.slice(i + 1) };
}

function nameMatches(pattern: string, name: string): boolean {
  return pattern.endsWith("*") ? name.startsWith(pattern.slice(0, -1)) : name === pattern;
}

function checkHealth(c: { health: string; stale?: boolean | undefined; state?: unknown }): Health {
  return c.stale || c.state ? "unknown" : normaliseHealth(c.health);
}

export function seatHealth(seat: Seat, modules: readonly ModuleTile[] | null): Health {
  if (modules === null) return "unknown";
  let worst: Health = "ok";
  const note = (h: Health): void => { if (RANK[h] > RANK[worst]) worst = h; };
  for (const pattern of seat.checks) {
    const { module, name } = split(pattern);
    const tile = modules.find((m) => m.module === module);
    if (!tile || !tile.enabled || tile.setup) { note("unknown"); continue; }
    const mine = tile.checks.filter((c) => nameMatches(name, c.name));
    if (mine.length === 0) { note("unknown"); continue; }
    for (const c of mine) note(checkHealth(c));
  }
  return worst;
}

export interface SeatCheck {
  id: string;
  label: string;
  health: Health;
  readAt: string | null;
  detail: string;
}

/** The checks to list on the card: the seat's own, with the time each read. */
export function seatChecks(seat: Seat, modules: readonly ModuleTile[] | null): SeatCheck[] {
  if (modules === null) return [];
  const out: SeatCheck[] = [];
  for (const pattern of seat.checks) {
    const { module, name } = split(pattern);
    const tile = modules.find((m) => m.module === module);
    if (!tile || !tile.enabled || tile.setup) continue;
    for (const c of tile.checks.filter((x) => nameMatches(name, x.name))) {
      const health = checkHealth(c);
      const label = /clips_/i.test(c.name) ? channelCheckLabel(c.name)
        : /ecc_/i.test(c.name) ? `Church ${channelCheckLabel(c.name, "ecc").toLowerCase()}`
        : safeText(c.name.replace(/_/g, " "));
      const why = c.state?.label ?? (c.stale ? "too old to count" : c.detail);
      out.push({
        id: `${module}.${c.name}`, label, health, readAt: c.observed_at,
        detail: health === "ok" ? "" : safeText(why.split(/\s\(|;|\n/)[0]?.trim()),
      });
    }
  }
  return out;
}

function time(at: string): number {
  const t = Date.parse(at);
  return Number.isNaN(t) ? -1e15 : t;
}

export const DONE_LIMIT = 8;

/** Finished actions of the seat, newest first, with how many were dropped as
 *  belonging to a retired lane. */
export function doneWithHidden(
  seat: Seat, rows: readonly ActivityRow[],
): { rows: ActivityRow[]; hidden: number } {
  const mine = rows.filter((r) => r.detail === "execute.finish"
    && seat.actionPrefix.some((p) => r.subject.startsWith(p)));
  const seen = visible(mine, (r) => ({ module: r.module, text: [r.subject] }));
  const sorted = [...seen.rows].sort((a, b) => time(b.at) - time(a.at));
  return { rows: sorted.slice(0, DONE_LIMIT), hidden: seen.hidden };
}

export function doneSoFar(seat: Seat, rows: readonly ActivityRow[]): ActivityRow[] {
  return doneWithHidden(seat, rows).rows;
}

/** The seat's newest finished action, or null when it has none. */
export function seatLastAction(seat: Seat, rows: readonly ActivityRow[]): ActivityRow | null {
  return doneWithHidden(seat, rows).rows[0] ?? null;
}

/** Open remediation tasks owned by the seat's agents (or its key); null when
 *  the board was not read. */
export function seatRemediation(
  seat: Seat, tasks: readonly Pick<RemediationTask, "owner" | "open">[] | null,
): number | null {
  return openCountFor([...seat.rosterAgents, seat.key], tasks);
}

export interface SeatRoster {
  runs24h: number;
  lastRunAt: string | null;
  ready: boolean;
}

/** null: the seat has no roster agent. "unknown": the roster was not read, or an agent is missing. */
export function seatRoster(seat: Seat, roster: readonly AgentSummary[] | null): SeatRoster | null | "unknown" {
  if (seat.rosterAgents.length === 0) return null;
  if (roster === null) return "unknown";
  const found = seat.rosterAgents.map((n) => roster.find((a) => a.name === n));
  const agents = found.filter((a): a is AgentSummary => a !== undefined);
  if (agents.length !== found.length) return "unknown";
  const times = agents.map((a) => a.last_run_at).filter((t): t is string => !!t);
  times.sort((a, b) => time(b) - time(a));
  return {
    runs24h: agents.reduce((n, a) => n + a.runs_24h, 0),
    lastRunAt: times[0] ?? null,
    ready: agents.every((a) => a.ready),
  };
}

/** Cards waiting on an answer that belong to the seat. */
export function seatWaiting(seat: Seat, decisions: readonly DecisionRow[]): DecisionRow[] {
  return seat.key === "academy"
    ? decisions.filter((d) => d.kind === "YouTube post" || d.kind === "Church post"
        || d.kind === "Reddit post")
    : decisions.filter((d) => seat.actionPrefix.some((p) => d.action.startsWith(p)));
}
