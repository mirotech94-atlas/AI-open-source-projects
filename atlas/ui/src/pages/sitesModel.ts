/**
 * The Client Sites tab's pure model: `/api/agency/sites` rows grouped by
 * health. UNKNOWN is its own group and is never counted as OK (invariant
 * 7); a reading older than twice the check interval is flagged stale; every
 * figure keeps the time it was read.
 */
import { normaliseHealth, type Health } from "../api";

export type SiteKind = "built" | "demo" | "app";

export interface SiteRow {
  slug: string;
  kind: SiteKind;
  hostname: string | null;
  source: string | null;
  managed: boolean;
  deployed_at: string | null;
  live_release: string | null;
  live_problem: string | null;
  health: string;
  health_detail: string;
  health_read_at: string | null;
  /** Client-site documents (agency.draft_doc); null or absent when they
   * could not be read, which renders UNKNOWN. */
  docs?: SiteDoc[] | null;
  docs_problem?: string | null;
}

export type SiteDocKind = "brief" | "handoff" | "report" | "proposal" | "client_guide";

/** A document drafted by the sites agent and published as a Claude Docs
 * artifact by the coding agent on the Mac. The link is its report, not
 * something ATLAS checked. */
export interface SiteDoc {
  id: number;
  kind: SiteDocKind;
  title: string;
  status: "draft" | "queued" | "published";
  artifact_url: string | null;
  reported_at: string | null;
  created_at: string | null;
  handoff_status: string | null;
}

export interface SitesOverview {
  sites: SiteRow[];
  read_at: string;
  check_every_s: number;
}

export interface SiteEvent {
  at: string | null;
  event: string;
  release: string | null;
  detail: string;
}

export interface SiteEvents {
  slug: string;
  events: SiteEvent[];
  read_at: string;
}

export const GROUP_ORDER: readonly Health[] = ["failing", "degraded", "unknown", "ok"];

export interface SiteGroups {
  failing: SiteRow[];
  degraded: SiteRow[];
  unknown: SiteRow[];
  ok: SiteRow[];
}

/** A reading older than twice the interval no longer says how the site is. */
export function isStale(row: SiteRow, now: Date, checkEveryS: number): boolean {
  if (!row.health_read_at) return true;
  const at = Date.parse(row.health_read_at);
  if (Number.isNaN(at)) return true;
  return now.getTime() - at > 2 * checkEveryS * 1000;
}

/** Group by health. A stale OK is not OK any more: it goes to UNKNOWN. */
export function groupSites(view: SitesOverview, now: Date): SiteGroups {
  const groups: SiteGroups = { failing: [], degraded: [], unknown: [], ok: [] };
  for (const row of view.sites) {
    let h = normaliseHealth(row.health);
    if (h === "ok" && isStale(row, now, view.check_every_s)) h = "unknown";
    groups[h].push(row);
  }
  for (const key of GROUP_ORDER) {
    groups[key].sort((a, b) => (a.hostname ?? a.slug).localeCompare(b.hostname ?? b.slug));
  }
  return groups;
}

export function okCount(groups: SiteGroups): number {
  return groups.ok.length;
}

export function summaryLine(groups: SiteGroups): string {
  const total = GROUP_ORDER.reduce((n, k) => n + groups[k].length, 0);
  if (total === 0) return "No client sites registered.";
  return `${total} site(s): ${groups.ok.length} OK, ${groups.degraded.length} degraded, ` +
    `${groups.failing.length} failing, ${groups.unknown.length} unknown`;
}

/** Newest first, whatever order they arrived in; an undated event last. */
export function orderEvents(events: SiteEvent[]): SiteEvent[] {
  const t = (e: SiteEvent): number => (e.at ? Date.parse(e.at) : Number.NaN);
  return [...events].sort((a, b) => {
    const ta = t(a);
    const tb = t(b);
    if (Number.isNaN(ta)) return Number.isNaN(tb) ? 0 : 1;
    if (Number.isNaN(tb)) return -1;
    return tb - ta;
  });
}

export function readLabel(iso: string | null): string {
  if (!iso) return "never read";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "read at an unknown time";
  return `read ${at.toLocaleString()}`;
}

export const KIND_LABEL: Record<SiteKind, string> = {
  built: "Built (Mapfront)",
  demo: "Demo",
  app: "App (watched)",
};

export const DOC_KIND_LABEL: Record<SiteDocKind, string> = {
  brief: "Brief",
  handoff: "Handoff",
  report: "Report",
  proposal: "Proposal",
  client_guide: "Client guide",
};

// The same rule as atlas/modules/agency/sitedocs.py: a claude.ai artifact
// link and nothing else is ever rendered as an href.
const ARTIFACT_URL =
  /^https:\/\/claude\.ai\/(?:artifact|code\/artifact)\/[A-Za-z0-9][A-Za-z0-9_-]{5,79}$/;

export function safeDocUrl(url: string | null | undefined): string | null {
  return url && ARTIFACT_URL.test(url) ? url : null;
}

export const DOC_STATUS_LABEL: Record<string, string> = {
  draft: "draft, waiting for its publish card",
  queued: "approved, waiting for the coding agent",
  published: "reported published",
};
