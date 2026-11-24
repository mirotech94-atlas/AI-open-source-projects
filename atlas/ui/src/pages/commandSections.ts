/**
 * The Command Center's six sections as pure functions (Miro, 2026-10-29:
 * "clear heading, brief statements"). Reads only. Every line keeps the time
 * it was read (invariant 6); anything not read is Unknown and never 0 or OK
 * (invariant 7).
 */
import { normaliseHealth, type AgencyActivity, type Health, type ModuleTile,
  type SeoPanelData } from "../api";
import {
  figurePart, leadClause, type FigurePart, type WeekDay, type WeekItem,
} from "./homeModel";
import { isHiddenModule, isRetiredText } from "./retired";
import { ROSTER_AGENTS } from "./roster";
import { isStale, safeDocUrl, type SiteDoc, type SitesOverview } from "./sitesModel";

// ---------------------------------------------------------------- today

export interface TodayLine {
  name: string;
  label: string;
  /** One short statement of what it did or is doing; null when not read. */
  text: string | null;
  health: Health;
  /** Runs yesterday; null when not read (Unknown, never 0). */
  runs: number | null;
  waiting: number | null;
  readAt: string | null;
}

/** One line per roster agent, always all nine: an agent the read did not
 *  return is Unknown, never left out. */
export function todayLines(read: AgencyActivity | null): TodayLine[] {
  const by = new Map((read?.agents ?? []).map((a) => [a.name, a]));
  return ROSTER_AGENTS.map((name): TodayLine => {
    const a = by.get(name);
    if (!a) {
      return { name, label: name, text: null, health: "unknown", runs: null, waiting: null,
               readAt: null };
    }
    const n = a.now;
    const last = n.last_run_known ? n.last_run : undefined;
    const job = n.running_jobs.find((j) => !isRetiredText(j));
    const text = n.headline?.trim()
      || (job ? `Running ${job}` : null)
      || (last ? `${last.failed ? "Last run failed" : "Last ran"}${last.at ? "" : ", time not recorded"}`
        : last === null ? "No run yet" : null);
    const health: Health = text === null ? "unknown"
      : last?.failed ? "failing" : a.issues.count > 0 ? "degraded" : "ok";
    return {
      name, label: a.label || name, text, health,
      runs: a.yesterday.runs, waiting: n.waiting, readAt: last?.at ?? n.read_at,
    };
  });
}

// ---------------------------------------------------------------- money

export const MONEY = {
  in: [
    { check: "shop.sales_7d", label: "Store revenue, 7 d" },
    { check: "shop.sales_count_7d", label: "Store sales, 7 d" },
    { check: "bizops.invoices_overdue", label: "Invoices overdue" },
  ],
  out: [
    { check: "content.higgsfield_budget", label: "Video spend" },
    { check: "assistant.budget", label: "Assistant AI budget" },
    { check: "perplexity.budget", label: "Search AI budget" },
    { check: "agency.prospect_budget", label: "Prospecting budget" },
    { check: "scrape.budget", label: "Scrape budget" },
  ],
} as const;

/** Each figure is one check's own value (never a number lifted from words). */
export function moneyFigures(
  modules: ModuleTile[] | null, error: string | null,
): { in: FigurePart[]; out: FigurePart[] } {
  const pick = (specs: ReadonlyArray<{ check: string; label: string }>): FigurePart[] =>
    specs.filter((s) => !isHiddenModule(s.check.split(".")[0]))
      .map((s) => figurePart(s, modules, error));
  return { in: pick(MONEY.in), out: pick(MONEY.out) };
}

// --------------------------------------------------------------- health

export interface HealthLine { module: string; title: string; health: Health; why: string;
  readAt: string | null }

const RANK: Record<Health, number> = { failing: 0, degraded: 1, unknown: 2, ok: 3 };

/** The modules that are not OK, worst first, one line each. A module set
 *  aside (not set up, paused) is left to the count, not listed. */
export function healthLines(modules: ModuleTile[] | null): HealthLine[] {
  if (modules === null) return [];
  return modules
    .filter((m) => m.enabled && !m.setup && !isHiddenModule(m.module))
    .map((m): HealthLine => {
      const h = normaliseHealth(m.health);
      const bad = m.checks.find((c) => (c.stale ? "unknown" : normaliseHealth(c.health)) === h);
      const why = bad ? `${bad.name}: ${bad.stale ? "reading too old"
        : leadClause(bad.detail, 60) || h}` : h;
      return { module: m.module, title: m.title || m.module, health: h, why,
               readAt: bad?.observed_at ?? m.last_check_at };
    })
    .filter((l) => l.health !== "ok")
    .sort((a, b) => RANK[a.health] - RANK[b.health] || a.module.localeCompare(b.module));
}

/** The short Command Center's Health lines (Miro 2026-10-29): FAILING and
 *  DEGRADED only, worst first. UNKNOWN is shown as a count chip beside them,
 *  never dropped and never as OK. */
export function alarmLines(modules: ModuleTile[] | null): HealthLine[] {
  return healthLines(modules).filter((l) => l.health === "failing" || l.health === "degraded");
}

/** Coming up as one line: the next `n` items from now, soonest first. */
export function nextUp(days: readonly WeekDay[], now: Date, n = 3): WeekItem[] {
  return days.flatMap((d) => d.items)
    .filter((i) => i.at.getTime() >= now.getTime())
    .sort((a, b) => a.at.getTime() - b.at.getTime())
    .slice(0, n);
}

// -------------------------------------------------------- search & sites

export interface SearchLine {
  domain: string;
  clicks: number | null;
  position: number | null;
  mobile: number | null;
  desktop: number | null;
  searchAt: string | null;
  vitalsAt: string | null;
}

export function searchLines(read: SeoPanelData | null): SearchLine[] {
  return (read?.sites ?? []).map((s) => ({
    domain: s.domain || s.slug,
    clicks: s.search?.clicks ?? null,
    position: s.search?.position ?? null,
    mobile: s.vitals?.mobile ?? null,
    desktop: s.vitals?.desktop ?? null,
    searchAt: s.search?.read_at ?? null,
    vitalsAt: s.vitals?.read_at ?? null,
  }));
}

export interface SiteLine {
  slug: string;
  host: string;
  health: Health;
  readAt: string | null;
  /** Every doc. `url` is a claude.ai artifact link only once published;
   *  a draft/queued doc (or a bad link) has null and shows Unknown, never
   *  hidden. null when the docs could not be read. */
  docs: Array<Pick<SiteDoc, "id" | "title" | "kind" | "status"> & { url: string | null }> | null;
}

/** A stale OK is Unknown, as on the Sites tab. */
export function siteLines(read: SitesOverview | null, now: Date): SiteLine[] {
  if (!read) return [];
  return read.sites.map((r) => {
    let h = normaliseHealth(r.health);
    if (h === "ok" && isStale(r, now, read.check_every_s)) h = "unknown";
    return {
      slug: r.slug, host: r.hostname ?? r.slug, health: h, readAt: r.health_read_at,
      docs: r.docs == null ? null : r.docs
        .map((d) => ({ id: d.id, title: d.title, kind: d.kind, status: d.status,
                       url: d.status === "published" ? safeDocUrl(d.artifact_url) : null })),
    };
  }).sort((a, b) => RANK[a.health] - RANK[b.health] || a.host.localeCompare(b.host));
}
