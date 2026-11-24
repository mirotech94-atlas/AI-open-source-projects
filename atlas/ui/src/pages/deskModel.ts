/**
 * ATLAS Agency: the Command Center's Agency desk sections, as pure functions.
 *
 * No DOM, no fetch; the clock only when passed. Every figure keeps the time
 * it was read; nothing here invents a number or a date. UNKNOWN has its own
 * treatment (grey, dashed, the word) and never shares OK's (invariant 7).
 * The tab only reads: decisions are answered in Slack.
 */
import {
  normaliseHealth, type ActivityRow, type AgentSummary, type Health, type ModuleTile,
  type Proposal, type ReportSummary,
} from "../api";
import type { SiteRow } from "./sitesModel";
import { outcomeDetail, outcomeLine } from "./outcomeText";
import { isRetiredText, safeText, visible } from "./retired";

// ------------------------------------------------------------ the frame

export const WORDMARK = "ATLAS Agency";

/** The Command Center's sections, in page order (the owner, 2026-10-28: what
 *  needs him and health first, then each agent, then calendar, reports and
 *  projects, settings last). Their ids are the page anchors (`/#clients`)
 *  and the only entries of the cmd-K palette. `decisions` and `activity`
 *  are the top grid's panels; the rest are the Agency desk's sections. */
export const SECTIONS = [
  { id: "decisions", label: "Decisions" },
  { id: "activity", label: "Activity & alerts" },
  { id: "team", label: "Team" },
  { id: "sales", label: "Sales" },
  { id: "seo", label: "SEO" },
  { id: "research", label: "Research" },
  { id: "remediation", label: "Remediation" },
  { id: "calendar", label: "Content calendar" },
  { id: "reports", label: "Reports" },
  { id: "clients", label: "Clients & Projects" },
  { id: "projects", label: "Projects" },
  { id: "settings", label: "Settings" },
] as const;

export type SectionId = (typeof SECTIONS)[number]["id"];
export type Section = (typeof SECTIONS)[number];

/** The palette's entries for a query: section ids only, in page order. */
export function paletteMatches(query: string): Section[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...SECTIONS];
  return SECTIONS.filter((s) => s.id.includes(q) || s.label.toLowerCase().includes(q));
}

/** What each section says when it read and found nothing: what is missing,
 *  never a placeholder row. */
export const EMPTY: Readonly<Record<SectionId, string>> = {
  clients: "No client or prospect yet: the agency module has not saved a lead.",
  projects: "No project yet: none has been opened on the project board.",
  decisions: "Nothing is waiting for an answer.",
  sales: "No lead yet and no sales card waiting.",
  seo: "No SEO audit or rank has been recorded yet.",
  research: "No research brief or competitor reading has been recorded yet.",
  remediation: "No remediation task: QA has not raised one.",
  calendar: "No tutorial has been posted and nothing is lined up.",
  activity: "Nothing logged yet.",
  reports: "No weekly report has been written yet.",
  team: "No agent is on the roster.",
  settings: "No connection is reporting.",
};

// --------------------------------------------------------------- health

export interface Treatment {
  className: string;
  word: string;
}

/** The one place a health becomes a class and a word. UNKNOWN is grey and
 *  dashed with the word visible; it never shares OK's class. */
export function healthTreatment(h: Health): Treatment {
  switch (h) {
    case "ok": return { className: "desk-h desk-h-ok", word: "OK" };
    case "degraded": return { className: "desk-h desk-h-degraded", word: "DEGRADED" };
    case "failing": return { className: "desk-h desk-h-failing", word: "FAILING" };
    default: return { className: "desk-h desk-h-unknown", word: "UNKNOWN" };
  }
}

// ------------------------------------------------- measured or not

/** One figure a section can show (SEO, Research). A row with no `read_at`
 *  is never drawn as a number: it says "not measured". */
export interface MeasureRow {
  label: string;
  value: number | null;
  unit: string;
  read_at: string | null;
}

export interface MeasuresRead {
  served_at: string;
  rows: MeasureRow[];
}

export type Measured =
  | { state: "measured"; label: string; value: number; unit: string; readAt: string }
  | { state: "not-measured"; label: string };

export function measured(rows: readonly MeasureRow[]): Measured[] {
  return rows.map((r) => r.read_at && r.value !== null && Number.isFinite(r.value)
    ? { state: "measured", label: safeText(r.label), value: r.value, unit: r.unit,
        readAt: r.read_at }
    : { state: "not-measured", label: safeText(r.label) });
}

// ------------------------------------------------------------- sales

export interface SalesSummary {
  stages: { key: Stage | "closed"; label: string; count: number }[];
  openCards: number | null;
}

const SALES_KINDS = new Set(["Sales email", "Proposal"]);

/** Leads by stage (the loaded page of leads), and the pending sales cards
 *  (null when the cards were not read). */
export function salesSummary(
  leads: readonly LeadLike[], decisions: readonly { kind: string }[] | null,
): SalesSummary {
  const counts = new Map<string, number>();
  for (const l of leads) {
    const st = statusStage(l.status);
    if (st && st !== "dropped") counts.set(st, (counts.get(st) ?? 0) + 1);
  }
  const keys: { key: Stage | "closed"; label: string }[] = [
    { key: "prospect", label: "Prospect" }, { key: "demo", label: "Demo" },
    { key: "closed", label: "Closed" },
  ];
  return {
    stages: keys.map((k) => ({ ...k, count: counts.get(k.key) ?? 0 })),
    openCards: decisions === null ? null
      : decisions.filter((d) => SALES_KINDS.has(d.kind)).length,
  };
}

// ---------------------------------------------- clients & projects board

export type Stage = "prospect" | "demo" | "awaiting" | "live";

export const STAGES: readonly { key: Stage; label: string }[] = [
  { key: "prospect", label: "Prospect" },
  { key: "demo", label: "Demo" },
  { key: "awaiting", label: "Awaiting approval" },
  { key: "live", label: "Live" },
];

/** Every `agency_leads.status` the schema allows (db/schema.sql). */
export const LEAD_STATUSES = ["new", "audited", "mocked", "contacted", "closed", "dropped"] as const;

/** A status alone: its stage, "closed" (behind the chip), "dropped"
 *  (hidden), or null for a status the schema does not name. */
export function statusStage(status: string): Stage | "closed" | "dropped" | null {
  switch (status.trim().toLowerCase()) {
    case "new":
    case "audited": return "prospect";
    case "mocked":
    case "contacted": return "demo";
    case "closed": return "closed";
    case "dropped": return "dropped";
    default: return null;
  }
}

/** `build.slugify` ends every site slug with `-<lead id>`. */
export function leadIdOfSlug(slug: string | null | undefined): number | null {
  const m = /-(\d+)$/.exec(slug ?? "");
  return m ? Number(m[1]) : null;
}

export interface LeadLike {
  id: number;
  business: string;
  category: string;
  status: string;
}

export interface BoardCardRow {
  id: number;
  name: string;
  category: string;
  status: string;
  stage: Stage | "closed";
  site: string | null;
  deployedAt: string | null;
  cardId: number | null;
}

export interface Board {
  columns: Record<Stage, BoardCardRow[]>;
  closed: BoardCardRow[];
  dropped: number;
  /** Leads whose status the schema does not name: counted, not placed. */
  unsorted: number;
}

type CardRef = Pick<Proposal, "id" | "action" | "params" | "expires_at"> & { status?: string };

function pendingDeployFor(cards: readonly CardRef[], now: number): Map<number, number> {
  const out = new Map<number, number>();
  for (const c of cards) {
    if (!c.action.startsWith("agency.deploy")) continue;
    if (c.status !== undefined && c.status !== "pending") continue;
    const exp = Date.parse(c.expires_at);
    if (!Number.isNaN(exp) && exp <= now) continue;   // expired never runs
    const slug = typeof c.params?.slug === "string" ? c.params.slug : null;
    const id = leadIdOfSlug(slug);
    if (id !== null && !out.has(id)) out.set(id, c.id);
  }
  return out;
}

/**
 * Leads into stages. Dropped is hidden; closed sits behind its chip; a
 * pending `agency.deploy*` card for the lead is Awaiting approval; a site
 * with `deployed_at` is Live; otherwise the status decides.
 */
export function buildBoard(
  leads: readonly LeadLike[], sites: readonly SiteRow[] | null,
  cards: readonly CardRef[] | null, now: number,
): Board {
  const deployed = new Map<number, SiteRow>();
  const anySite = new Map<number, SiteRow>();
  for (const s of sites ?? []) {
    const id = leadIdOfSlug(s.slug);
    if (id === null) continue;
    if (!anySite.has(id)) anySite.set(id, s);
    if (s.deployed_at && !deployed.has(id)) deployed.set(id, s);
  }
  const pending = pendingDeployFor(cards ?? [], now);
  const board: Board = {
    columns: { prospect: [], demo: [], awaiting: [], live: [] },
    closed: [], dropped: 0, unsorted: 0,
  };
  for (const l of leads) {
    const base = statusStage(l.status);
    if (base === "dropped") { board.dropped += 1; continue; }
    const site = deployed.get(l.id) ?? anySite.get(l.id) ?? null;
    const card = pending.get(l.id) ?? null;
    const row = (stage: Stage | "closed"): BoardCardRow => ({
      id: l.id, name: l.business, category: l.category, status: l.status, stage,
      site: site?.slug ?? null, deployedAt: site?.deployed_at ?? null, cardId: card,
    });
    if (base === "closed") { board.closed.push(row("closed")); continue; }
    if (card !== null) { board.columns.awaiting.push(row("awaiting")); continue; }
    if (deployed.has(l.id)) { board.columns.live.push(row("live")); continue; }
    if (base === null) { board.unsorted += 1; continue; }
    board.columns[base].push(row(base));
  }
  return board;
}

export function boardIsEmpty(b: Board): boolean {
  return STAGES.every((s) => b.columns[s.key].length === 0) && b.closed.length === 0;
}

// ---------------------------------------------------- content calendar

export interface CalendarPostIn {
  episode: number;
  title: string;
  video_id: string;
  at: string | null;
  public: boolean;
}

export interface AcademyCalendar {
  as_of: string;
  read_at: string;
  series: { key: string; name: string; posted: CalendarPostIn[];
            next_up: { episode: number; title: string }[] }[];
  next_up: { series: string; episode: number; title: string }[];
  notes: string[];
}

export interface CalendarPosted {
  episode: number;
  title: string;
  at: string | null;
  public: boolean;
}

/** A next-up episode has no date, so the type has no field for one. */
export interface CalendarNext {
  episode: number;
  title: string;
}

export interface CalendarGroup {
  key: string;
  name: string;
  posted: CalendarPosted[];
  nextUp: CalendarNext[];
}

function at(iso: string | null): number {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? -1e15 : t;
}

/** Series with something posted or lined up, catalog order; posted rows
 *  newest first; next-up in today's lineup order, with no date. */
export function calendarGroups(cal: AcademyCalendar): CalendarGroup[] {
  return cal.series
    .map((s): CalendarGroup => ({
      key: s.key,
      name: safeText(s.name),
      posted: [...s.posted]
        .sort((a, b) => at(b.at) - at(a.at))
        .map((p) => ({ episode: p.episode, title: safeText(p.title), at: p.at, public: p.public })),
      nextUp: s.next_up.map((n) => ({ episode: n.episode, title: safeText(n.title) })),
    }))
    .filter((g) => g.posted.length > 0 || g.nextUp.length > 0);
}

// ---------------------------------------------------------- activity

/** Activity rows by module, newest first in each; retired lanes dropped
 *  and counted. */
export function activityBySource(
  rows: readonly ActivityRow[],
): { sources: { module: string; rows: ActivityRow[] }[]; hidden: number } {
  const seen = visible(rows, (r) => ({ module: r.module, text: [r.subject] }));
  const by = new Map<string, ActivityRow[]>();
  for (const r of seen.rows) {
    const list = by.get(r.module) ?? [];
    list.push(r);
    by.set(r.module, list);
  }
  const sources = [...by.entries()]
    .map(([module, list]) => ({ module, rows: list.sort((a, b) => at(b.at) - at(a.at)) }))
    .sort((a, b) => a.module.localeCompare(b.module));
  return { sources, hidden: seen.hidden };
}

export function weeklyReports(rows: readonly ReportSummary[]): { rows: ReportSummary[]; hidden: number } {
  const seen = visible(rows.filter((r) => r.kind === "weekly"),
                       (r) => ({ text: [r.title, r.excerpt] }));
  return { rows: seen.rows.sort((a, b) => at(b.at) - at(a.at)), hidden: seen.hidden };
}

// ---------------------------------------------------------------- team

export type TeamStatus = "ok" | "idle" | "failing" | "unknown";

export interface TeamRow {
  key: string;
  title: string;
  /** One short line on what the agent does; "" when the server sends none. */
  role: string;
  /** The roster's status; a missing one (older server, Academy seat) is UNKNOWN. */
  status: TeamStatus;
  /** null: not known (the Academy seat has no roster agent). */
  ready: boolean | null;
  lastRunAt: string | null;
  lastOutcome: string | null;
  /** The last outcome as one clean line (<= 90 chars), never raw model prose. */
  line: string | null;
  /** The same clean text, longer, shown only when the row is expanded. */
  detail: string | null;
  nextShiftAt: string | null;
}

const TEAM_STATUSES: readonly TeamStatus[] = ["ok", "idle", "failing", "unknown"];

function teamStatus(s: string | undefined): TeamStatus {
  return TEAM_STATUSES.includes(s as TeamStatus) ? (s as TeamStatus) : "unknown";
}

export const ACADEMY_SEAT = "Academy YouTube";

/** The roster, retired lanes dropped, plus the Academy seat, whose last
 *  action comes from the activity log (`academy.` actions). */
export function teamRows(
  agents: readonly AgentSummary[] | null, activity: readonly ActivityRow[] | null,
): { rows: TeamRow[]; hidden: number } {
  const seen = agents ? visible(agents, (a) => ({ module: a.name, text: [a.name, a.title] }))
    : { rows: [], hidden: 0 };
  const rows: TeamRow[] = seen.rows.map((a) => ({
    key: a.name, title: a.title, role: a.role ?? "", status: teamStatus(a.status),
    ready: a.ready, lastRunAt: a.last_run_at, lastOutcome: a.last_outcome,
    line: outcomeLine(a.last_outcome),
    detail: outcomeDetail(a.last_outcome_detail ?? a.last_outcome),
    nextShiftAt: a.next_shift_at,
  }));
  const academy = (activity ?? [])
    .filter((r) => r.subject.startsWith("academy.") || r.module === "academy")
    .filter((r) => !isRetiredText(r.subject))
    .sort((a, b) => at(b.at) - at(a.at))[0] ?? null;
  rows.push({
    key: "academy", title: ACADEMY_SEAT, role: "Shorts and tutorials", status: "unknown",
    ready: null,
    lastRunAt: activity ? academy?.at ?? null : null,
    lastOutcome: academy ? academy.subject : null,
    line: academy ? outcomeLine(academy.subject) : null,
    detail: null,
    nextShiftAt: null,
  });
  return { rows, hidden: seen.hidden };
}

// ------------------------------------------------------------ settings

export const CONNECTIONS = ["youtube", "slack", "mail", "calendar"] as const;

export interface ConnectionCheck {
  name: string;
  health: Health;
  readAt: string | null;
  detail: string;
}

export interface ConnectionRow {
  module: string;
  health: Health;
  checks: ConnectionCheck[];
}

/** Connection health for youtube, slack (with `slack.socket`), mail and
 *  calendar. A module or the socket check that is not reporting is
 *  UNKNOWN, with the reason. */
export function connectionRows(modules: readonly ModuleTile[] | null): ConnectionRow[] {
  return CONNECTIONS.map((name): ConnectionRow => {
    const tile = modules?.find((m) => m.module === name) ?? null;
    if (!tile) {
      return { module: name, health: "unknown", checks: [{
        name: "module", health: "unknown", readAt: null,
        detail: modules ? "not reporting" : "the overview could not be read",
      }] };
    }
    const checks: ConnectionCheck[] = tile.checks
      .filter((c) => !isRetiredText(c.name.replace(/^clips_/, "")))
      .map((c) => ({
        name: safeText(c.name), health: normaliseHealth(c.health),
        readAt: c.read_at ?? c.observed_at, detail: safeText(c.detail),
      }));
    if (name === "slack" && !tile.checks.some((c) => c.name === "socket")) {
      checks.push({ name: "socket", health: "unknown", readAt: null,
                    detail: "no reading of the Socket Mode link" });
    }
    return { module: name, health: normaliseHealth(tile.health), checks };
  });
}

export interface SalesReading {
  /** null: the sales module is not reporting this check ("not measured"). */
  health: Health | null;
  value: number | null;
  readAt: string | null;
}

/** One `sales.*` check from the overview: its health, number and read time.
 *  A reading that is not OK carries no number (never a stale figure). */
export function salesReading(modules: readonly ModuleTile[] | null, check: string): SalesReading {
  const c = modules?.find((m) => m.module === "sales")?.checks.find((k) => k.name === check);
  if (!c) return { health: null, value: null, readAt: null };
  const health = c.stale ? "unknown" : normaliseHealth(c.health);
  const value = health === "ok" && typeof c.value === "number" ? c.value : null;
  return { health, value, readAt: c.read_at ?? c.observed_at };
}

export type SecretState = "set" | "unset" | "unknown";

export interface SecretRow {
  name: string;
  state: SecretState;
}

/** Names and set/unset only. A value that is not a boolean is UNKNOWN and
 *  is never carried, so it can never be shown. */
export function secretRows(secrets: Readonly<Record<string, unknown>> | null): SecretRow[] {
  if (!secrets) return [];
  return Object.keys(secrets).map((name) => {
    const v = secrets[name];
    return { name, state: v === true ? "set" : v === false ? "unset" : "unknown" };
  });
}

export const SECRET_WORD: Readonly<Record<SecretState, string>> = {
  set: "set", unset: "not set", unknown: "UNKNOWN",
};
