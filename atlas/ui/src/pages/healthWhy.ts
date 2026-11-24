/**
 * "Why does ATLAS read FAILING?": the checks behind the header pill and the
 * status ring, as pure functions. The same modules the pill counts go in, so
 * the list and the pill can never disagree about what is wrong.
 *
 * Nothing is hidden or downgraded: every failing and degraded check is listed
 * (failing first), every unknown one is grouped by module. A check set aside
 * (not set up / paused) is out of the pill's count and so out of this list;
 * the list says how many were set aside. Each row carries the time that
 * check was read (invariant 6); one that was never read says so.
 */
import { normaliseHealth, type ModuleTile } from "../api";

export const WHY_DETAIL_CHARS = 100;

export interface WhyRow {
  module: string;
  check: string;
  health: "failing" | "degraded" | "unknown";
  /** The check's own words, first 100 characters. */
  detail: string;
  /** ISO time of the reading, or null when it was never read. */
  readAt: string | null;
  /** The reading is too old to count: UNKNOWN underneath. */
  stale: boolean;
}

export interface WhyGroup {
  module: string;
  rows: WhyRow[];
}

export interface WhyList {
  /** Failing checks, then degraded ones, each in module order. */
  attention: WhyRow[];
  /** Unknown checks grouped by module. */
  unknown: WhyGroup[];
  unknownCount: number;
  /** Checks left out because they are set aside (not set up, paused). */
  setAside: number;
}

export function clip(text: string | null | undefined, max = WHY_DETAIL_CHARS): string {
  const t = (text ?? "").replace(/\s+/g, " ").trim();
  if (t === "") return "no detail given";
  return t.length > max ? `${t.slice(0, max)}…` : t;
}

export function whyList(modules: ModuleTile[] | null): WhyList {
  const out: WhyList = { attention: [], unknown: [], unknownCount: 0, setAside: 0 };
  if (modules === null) return out;
  const failing: WhyRow[] = [];
  const degraded: WhyRow[] = [];
  for (const m of modules.filter((x) => x.enabled)) {
    if (m.setup) { out.setAside += m.checks.length || 1; continue; }
    const unknownRows: WhyRow[] = [];
    for (const c of m.checks) {
      if (c.state) { out.setAside += 1; continue; }
      const health = normaliseHealth(c.health);
      if (health === "ok") continue;
      const row: WhyRow = {
        module: m.module, check: c.name, health, detail: clip(c.detail),
        readAt: c.observed_at ?? c.read_at ?? null, stale: c.stale === true,
      };
      if (health === "failing") failing.push(row);
      else if (health === "degraded") degraded.push(row);
      else unknownRows.push(row);
    }
    if (unknownRows.length > 0) {
      out.unknown.push({ module: m.module, rows: unknownRows });
      out.unknownCount += unknownRows.length;
    }
    // A module the pill counts as failing/unknown with no check rows to show
    // for it is still named, so a count never has nothing behind it.
    if (m.checks.length === 0 && normaliseHealth(m.health) !== "ok") {
      const health = normaliseHealth(m.health) as WhyRow["health"];
      const row: WhyRow = { module: m.module, check: "(module)", health,
        detail: clip(m.latest_finding?.title ?? "the module reports no checks"),
        readAt: m.last_check_at, stale: false };
      if (health === "failing") failing.push(row);
      else if (health === "degraded") degraded.push(row);
      else { out.unknown.push({ module: m.module, rows: [row] }); out.unknownCount += 1; }
    }
  }
  out.attention = [...failing, ...degraded];
  return out;
}

/** "10:42:07" in the reader's clock; "never read" for no reading. */
export function readTime(at: Date | string | null): string {
  if (!at) return "never read";
  const d = typeof at === "string" ? new Date(at) : at;
  if (Number.isNaN(d.getTime())) return "never read";
  return `read ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}`;
}
