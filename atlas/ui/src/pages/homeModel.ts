/**
 * The home page's words and order, as pure functions (no DOM, no fetch,
 * the clock only when passed), so the rules it keeps are tested once:
 *
 * - A failed read is UNKNOWN, never the figure from the read before it
 *   (`useFresh` keeps old data after an error; `readOf` drops it).
 * - A check that is missing, stale or itself unknown is UNKNOWN, with the
 *   reason in words -- never OK, never a zero (invariant 7).
 * - Figures are the check's own `value` and unit with the time the check
 *   read them. Nothing here turns prose into a number (invariant 6): a
 *   check with no value shows its words, without a big number.
 * - The diary is read a week ahead (`WEEK_HOURS`). A day past the horizon
 *   the server says it covered is "not read", never an empty day: an empty
 *   day would claim nothing is on. Nor is a day free when a module's own
 *   agenda could not be read (`unread`): it is "partly unknown".
 */
import {
  normaliseHealth, type Agenda, type AgendaEntry, type Board, type BoardAgent,
  type BoardCard, type BoardJob, type CheckSummary, type Health, type ModuleTile,
  type WorkflowGraph, type WorkflowOutcome,
} from "../api";
import { formatValue } from "./liveModel";

// ------------------------------------------------------------------ reads

/** The part of `useFresh` this file needs. */
export interface Read<T> {
  data: T | null;
  error: string | null;
  fetchedAt: Date | null;
}

/** Data only from a read that succeeded; the error otherwise. */
export function readOf<T>(read: Read<T>): { data: T | null; error: string | null } {
  if (read.error !== null) return { data: null, error: read.error };
  return { data: read.data, error: null };
}

// -------------------------------------------------------------- marketing

export type FigureState = "ok" | "warn" | "unknown";

/** One number on a tile: one module check's own reading. */
export interface FigurePart {
  /** "clips.clips_posted" */
  check: string;
  module: string;
  name: string;
  /** What the number counts ("posted", "revenue"). */
  label: string;
  state: FigureState;
  /** The health word shown beside a number that is not OK. */
  word: string;
  /** The reading's own value, only when it counts (never for UNKNOWN). */
  value: number | null;
  unit: string | null;
  /** The value as text, or null when there is none to show. */
  display: { text: string; unit: string | null } | null;
  /** Why it is UNKNOWN; or, for an older server with no value, the
   *  check's own words. Empty when the number says it. */
  text: string;
  /** When the check read it. Null when no reading exists. */
  readAt: Date | null;
  readAtIso: string | null;
  health: Health;
}

export interface Figure {
  key: string;
  label: string;
  to: string;
  parts: FigurePart[];
  /** Extra lines (the per-product sales), as the check wrote them. */
  lines: string[];
  /** What a point on its chart is, when that is not obvious. */
  chartNote: string | null;
}

/** The marketing tiles. Every number is one module check's own `value`;
 *  none is lifted out of a sentence (invariant 6). */
export const MARKETING_TILES = [
  { key: "reels", label: "Reels posted, last 30 days", to: "/marketing", chartNote: null,
    parts: [{ check: "content.reel_metrics", label: "posts" }] },
  { key: "clips", label: "Clips", to: "/marketing", chartNote: null,
    parts: [{ check: "clips.clips_posted", label: "posted" },
            { check: "clips.clips_earnings", label: "submitted to Whop" }] },
  { key: "leads", label: "Leads waiting", to: "/leads", chartNote: null,
    parts: [{ check: "agency.leads_waiting", label: "waiting" }] },
  { key: "sales", label: "Store sales, 7 days", to: "/marketing",
    chartNote: "each point is the 7-day total as read then",
    parts: [{ check: "shop.sales_count_7d", label: "sales" },
            { check: "shop.sales_7d", label: "revenue" }] },
] as const;

/** The detail's first clause: up to the first bracket, semicolon, full stop
 *  or line break. The words are the check's own; only the tail is cut. */
export function leadClause(detail: string, max = 96): string {
  const cut = detail.split(/\s\(|;|\.\s|\n/)[0]?.trim() ?? "";
  return cut.length > max ? `${cut.slice(0, max - 1)}…` : cut;
}

/** `shop.sales_7d` writes one clause per product, joined by "; ", and a
 *  trailing "(last 7 days)". Each product is its own line: the first clause
 *  alone would show one product as if it were the store. */
export function salesLines(detail: string): string[] {
  return detail
    .replace(/\s*\(last 7 days\)\s*$/, "")
    .split(/;\s*/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function findCheck(modules: ModuleTile[], key: string): CheckSummary | undefined {
  const [mod, name] = key.split(".");
  return modules.find((m) => m.module === mod)?.checks.find((c) => c.name === name);
}

/** One check's reading as a number on a tile, or UNKNOWN with the reason. */
export function figurePart(
  spec: { check: string; label: string },
  modules: ModuleTile[] | null,
  overviewError: string | null,
): FigurePart {
  const [module = "", name = ""] = spec.check.split(".");
  const base = { check: spec.check, module, name, label: spec.label };
  const unknown = (text: string, readAt: Date | null, readAtIso: string | null): FigurePart => ({
    ...base, state: "unknown", word: "Unknown", value: null, unit: null, display: null,
    text, readAt, readAtIso, health: "unknown",
  });
  if (modules === null) {
    return unknown(overviewError ? `could not read the checks: ${overviewError}` : "not read yet",
                   null, null);
  }
  const check = findCheck(modules, spec.check);
  if (!check) return unknown("this check is not reporting", null, null);
  const readAtIso = check.read_at ?? check.observed_at ?? null;
  const readAt = readAtIso ? new Date(readAtIso) : null;
  const health: Health = check.stale ? "unknown" : normaliseHealth(check.health);
  if (health === "unknown") {
    const why = check.stale
      ? "the last reading is too old to count"
      : check.state?.label ?? (leadClause(check.detail) || "could not be measured");
    return unknown(why, readAt, readAtIso);
  }
  const value = typeof check.value === "number" && Number.isFinite(check.value)
    ? check.value : null;
  const unit = check.unit ?? null;
  return {
    ...base,
    state: health === "ok" ? "ok" : "warn",
    word: health === "ok" ? "OK" : health === "degraded" ? "Degraded" : "Failing",
    value, unit,
    display: value === null ? null : formatValue(value, unit),
    // No number from the server (an older one): the check's own words,
    // never a number read out of them.
    text: value === null ? (leadClause(check.detail) || health) : "",
    readAt, readAtIso, health,
  };
}

export function marketingFigures(
  modules: ModuleTile[] | null,
  overviewError: string | null,
): Figure[] {
  return MARKETING_TILES.map((spec) => {
    const parts = spec.parts.map((p) => figurePart(p, modules, overviewError));
    // The per-product lines are the dollars check's own words, shown only
    // while that check counts and names more than one product.
    const dollars = spec.key === "sales" && modules ? findCheck(modules, "shop.sales_7d") : undefined;
    const dollarsPart = parts.find((p) => p.check === "shop.sales_7d");
    const lines = dollars && dollarsPart && dollarsPart.state !== "unknown"
      ? salesLines(dollars.detail) : [];
    return {
      key: spec.key, label: spec.label, to: spec.to, parts,
      lines: lines.length > 1 ? lines : [], chartNote: spec.chartNote,
    };
  });
}

// ----------------------------------------------------------------- health

export interface ModuleRow {
  module: string;
  title: string;
  health: Health;
  /** "not set up" / "paused until ..." -- grey, and out of the counts. */
  setAside: string | null;
  finding: string | null;
  lastAt: string | null;
}

export interface HealthSummary {
  rows: ModuleRow[];
  counts: Record<Health, number>;
  setAside: number;
  /** null: the overview could not be read, so nothing is known. */
  known: boolean;
}

const RANK: Record<Health, number> = { failing: 0, degraded: 1, unknown: 2, ok: 3 };

/** Worst first, set-aside modules last; ties keep the server's order. */
export function healthSummary(modules: ModuleTile[] | null): HealthSummary {
  const counts: Record<Health, number> = { ok: 0, degraded: 0, failing: 0, unknown: 0 };
  if (modules === null) return { rows: [], counts, setAside: 0, known: false };
  const rows: ModuleRow[] = modules
    .filter((m) => m.enabled)
    .map((m) => ({
      module: m.module,
      title: m.title || m.module,
      health: normaliseHealth(m.health),
      setAside: m.setup ?? null,
      finding: m.latest_finding?.title ?? null,
      lastAt: m.last_check_at,
    }));
  let setAside = 0;
  for (const r of rows) {
    if (r.setAside) setAside += 1;
    else counts[r.health] += 1;
  }
  const order = rows.map((r, i) => ({ r, i }));
  order.sort((a, b) =>
    Number(Boolean(a.r.setAside)) - Number(Boolean(b.r.setAside))
    || RANK[a.r.health] - RANK[b.r.health]
    || a.i - b.i);
  return { rows: order.map((x) => x.r), counts, setAside, known: true };
}

/** One sentence for the strip, every state named in words. */
export function healthLine(s: HealthSummary): string {
  if (!s.known) return "Health unknown: the checks could not be read.";
  const c = s.counts;
  const parts: string[] = [];
  if (c.failing) parts.push(`${c.failing} failing`);
  if (c.degraded) parts.push(`${c.degraded} degraded`);
  if (c.unknown) parts.push(`${c.unknown} unknown`);
  parts.push(`${c.ok} OK`);
  if (s.setAside) parts.push(`${s.setAside} not set up or paused`);
  return parts.join(" · ");
}

// ------------------------------------------------------------- needs you

export interface WaitingCard extends BoardCard {
  who: string;
}

/** Every card waiting on the owner, the soonest to expire first, already-expired
 *  last (they cannot run: silence expired them). Null: unknown. */
export function waitingCards(board: Board | null, now: number): WaitingCard[] | null {
  if (!board || board.totals.waiting_on_you === null) return null;
  // A card shared by two seats is one decision: listed once, under the
  // first seat that holds it.
  const seen = new Set<number>();
  const cards = board.agents.flatMap((a) =>
    (a.waiting ?? []).map((c) => ({ ...c, who: a.label })))
    .filter((c) => (seen.has(c.id) ? false : (seen.add(c.id), true)));
  const priority = (iso: string | null): [number, number] => {
    const t = iso ? Date.parse(iso) : NaN;
    if (Number.isNaN(t)) return [1, 0];
    return t > now ? [0, t] : [2, t];
  };
  return cards.sort((a, b) => {
    const x = priority(a.expires_at);
    const y = priority(b.expires_at);
    return x[0] - y[0] || x[1] - y[1] || a.id - b.id;
  });
}

// ------------------------------------------------------------------ week

export type WeekKind = "event" | "deadline" | "job" | "card";

export interface WeekItem {
  at: Date;
  title: string;
  kind: WeekKind;
  detail: string;
  source: string;
}

/** read: the diary covers the whole day. partial: it stops part-way, at
 *  `until`. beyond: not read at all. unknown: the diary read failed. */
export type DiaryCover = "read" | "partial" | "beyond" | "unknown";

export interface WeekDay {
  date: Date;
  key: string;
  isToday: boolean;
  diary: DiaryCover;
  until: Date | null;
  /** Modules whose agenda could not be read this time: what this day shows
   *  may be missing theirs, so "Nothing on" is never said. */
  unread: string[];
  items: WeekItem[];
}

/** How far ahead the home page reads the diary: the whole week. */
export const WEEK_HOURS = 168;

export function startOfDay(d: Date): Date {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

function addDays(d: Date, n: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

function agendaKind(entry: AgendaEntry): WeekKind {
  return entry.kind === "event" ? "event" : "deadline";
}

/** The next run of every scheduled job, once each, as the server computed
 *  it (`next_due`). Never worked out here from an hour of the box's clock. */
export function jobsDue(agents: BoardAgent[]): BoardJob[] {
  const seen = new Map<string, BoardJob>();
  for (const a of agents) {
    for (const j of a.jobs) if (j.next_due && !seen.has(j.name)) seen.set(j.name, j);
  }
  return [...seen.values()].sort(
    (a, b) => Date.parse(a.next_due ?? "") - Date.parse(b.next_due ?? ""));
}

/**
 * Seven days from today, each with what lands on it: the diary's events and
 * deadlines, the cards' expiry, and the jobs' next run.
 *
 * `agendaReadAt` is when the diary was read: its horizon runs from then,
 * not from now, so a page left open does not stretch the read.
 */
export function weekDays({
  now, agenda, agendaError, agendaReadAt, board, days = 7,
}: {
  now: Date;
  agenda: Agenda | null;
  agendaError: string | null;
  agendaReadAt: Date | null;
  board: Board | null;
  days?: number;
}): WeekDay[] {
  const first = startOfDay(now);
  const diaryFailed = agendaError !== null || agenda === null || agenda.unavailable !== null;
  const horizonEnd = !diaryFailed && agenda && agendaReadAt
    ? new Date(agendaReadAt.getTime() + agenda.horizon_hours * 3_600_000)
    : null;

  const unread = diaryFailed ? [] : agenda?.unread ?? [];
  const out: WeekDay[] = [];
  for (let i = 0; i < days; i += 1) {
    const date = addDays(first, i);
    const end = addDays(first, i + 1);
    let diary: DiaryCover = "unknown";
    let until: Date | null = null;
    if (horizonEnd) {
      if (horizonEnd >= end) diary = "read";
      else if (horizonEnd > date) { diary = "partial"; until = horizonEnd; }
      else diary = "beyond";
    }
    out.push({
      date, key: date.toDateString(), isToday: i === 0, diary, until,
      unread: diary === "beyond" || diary === "unknown" ? [] : unread, items: [],
    });
  }
  const last = addDays(first, days);
  const place = (item: WeekItem): void => {
    if (item.at < first || item.at >= last) return;
    const day = out.find((d) => d.key === item.at.toDateString());
    day?.items.push(item);
  };

  if (!diaryFailed) {
    for (const e of agenda?.items ?? []) {
      place({ at: new Date(e.when), title: e.title, kind: agendaKind(e),
              detail: e.detail, source: e.source });
    }
  }
  if (board) {
    for (const j of jobsDue(board.agents)) {
      place({ at: new Date(j.next_due ?? ""), title: j.name.replace(/_/g, " "),
              kind: "job", detail: j.when, source: "schedule" });
    }
    for (const c of waitingCards(board, now.getTime()) ?? []) {
      if (!c.expires_at) continue;
      place({ at: new Date(c.expires_at), title: `Card #${c.id} expires`,
              kind: "card", detail: c.action, source: c.who });
    }
  }
  for (const d of out) d.items.sort((a, b) => a.at.getTime() - b.at.getTime());
  return out;
}

// -------------------------------------------------------------- workflows

export interface FlowRow {
  id: string;
  title: string;
  outcome: WorkflowOutcome;
  at: string | null;
  trigger: string;
}

const FLOW_RANK: Record<WorkflowOutcome, number> = {
  failed: 0, degraded: 1, unknown: 2, never: 3, ok: 4,
};

/** Trouble first, then the most recent run. */
export function flowRows(graphs: WorkflowGraph[]): FlowRow[] {
  return graphs
    .map((g) => ({ id: g.id, title: g.title, outcome: g.last_run.outcome,
                   at: g.last_run.at, trigger: g.trigger_detail }))
    .sort((a, b) => FLOW_RANK[a.outcome] - FLOW_RANK[b.outcome]
      || Date.parse(b.at ?? "1970") - Date.parse(a.at ?? "1970"));
}

/** The canvas's colour words, the same four the Workflows page draws. */
export function flowHealth(o: WorkflowOutcome): Health {
  if (o === "ok") return "ok";
  if (o === "degraded") return "degraded";
  if (o === "failed") return "failing";
  return "unknown";
}

// ---------------------------------------------------------------- agents

export interface BusySeat {
  name: string;
  label: string;
  title: string;
  headline: string;
  inFlight: number | null;
  waiting: number | null;
}

/** Who has work in hand, most first, and what it is: the board's own
 *  headline. Unknown counts are kept as null and shown as UNKNOWN. */
export function busySeats(board: Board | null): BusySeat[] {
  if (!board) return [];
  return board.agents
    .filter((a) => (a.counts.in_flight ?? 0) > 0 || a.counts.in_flight === null)
    .sort((a, b) => (b.counts.in_flight ?? 0) - (a.counts.in_flight ?? 0))
    .map((a) => ({
      name: a.name, label: a.label, title: a.title, headline: a.headline,
      inFlight: a.counts.in_flight, waiting: a.counts.waiting,
    }));
}

// ------------------------------------------------------------------ words

/** Floored: 90 seconds is "1m ago", never "2m ago". */
export function rel(iso: string | Date | null, now: number = Date.now()): string {
  if (!iso) return "never";
  const t = typeof iso === "string" ? Date.parse(iso) : iso.getTime();
  if (Number.isNaN(t)) return "never";
  const delta = now - t;
  const mins = Math.floor(Math.abs(delta) / 60000);
  const text = mins < 1 ? "<1m"
    : mins < 60 ? `${mins}m`
    : mins < 1440 ? `${Math.floor(mins / 60)}h`
    : `${Math.floor(mins / 1440)}d`;
  return delta >= 0 ? `${text} ago` : `in ${text}`;
}

export function hhmm(d: Date | string | null): string {
  if (!d) return "—";
  const x = typeof d === "string" ? new Date(d) : d;
  return x.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

/** "read 10:42", or "not read yet". Every figure carries one. */
export function readLine(at: Date | null): string {
  return at ? `read ${hhmm(at)}` : "not read yet";
}

/** The figure's status line in plain words. */
export const STATUS_WORDS: Record<string, string> = {
  IDLE: "Idle",
  LISTENING: "Listening",
  THINKING: "Thinking",
  SPEAKING: "Speaking",
};
