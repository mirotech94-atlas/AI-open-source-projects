/**
 * The SEO panel beside the Agency map, as pure functions, from
 * `GET /api/agency/seo`: per site, Search Console clicks, position and
 * counts and the PageSpeed scores, each with its read time. A null figure
 * is UNKNOWN (grey, dashed, the word), never 0 and never OK. Counts,
 * scores, names and times only.
 */
import type { SeoCheckReading, SeoPanelData, SeoSite } from "../api";
import { hhmm } from "./homeModel";
import type { Line } from "./agentActivityModel";

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? "" : "s"}`;
const num = (v: number | null, what: string): string => (v === null ? `${what} not measured` : `${what} ${v}`);

const CHECK_NAMES = { search_console: "Search Console", core_web_vitals: "PageSpeed" } as const;

function checkLine(name: string, c: SeoCheckReading | null): Line {
  if (c === null) return { text: `${name}: not read yet`, health: "unknown" };
  const at = c.at ? ` ${hhmm(c.at)}` : "";
  if (c.health === "ok") return { text: `${name}: read${at}`, health: "plain" };
  return { text: `${name}: ${c.health}${at}${c.why ? ` (${c.why})` : ""}`, health: c.health };
}

/** "Doing": the two collectors' last readings. */
export function checkLines(d: SeoPanelData): Line[] {
  return (Object.keys(CHECK_NAMES) as (keyof typeof CHECK_NAMES)[]).map(
    (k) => checkLine(CHECK_NAMES[k], d.checks[k]));
}

export function siteLines(s: SeoSite): Line[] {
  const out: Line[] = [];
  const sc = s.search;
  if (sc === null) out.push({ text: "Search Console: not measured", health: "unknown" });
  else {
    out.push({ text: `${sc.clicks === null ? "clicks not measured" : plural(sc.clicks, "click")} in 28 days, `
      + `${num(sc.position, "position")} (${hhmm(sc.read_at)})`, health: "plain" });
    if (sc.striking !== null && sc.striking > 0) {
      out.push({ text: `${sc.striking} striking-distance ${sc.striking === 1 ? "query" : "queries"}`,
                 health: "plain" });
    }
    if (sc.losing !== null && sc.losing > 0) {
      out.push({ text: `${plural(sc.losing, "page")} losing clicks`, health: "degraded" });
    }
  }
  const v = s.vitals;
  if (v === null) out.push({ text: "Speed: not measured", health: "unknown" });
  else {
    out.push({ text: `speed ${num(v.mobile, "mobile")}, ${num(v.desktop, "desktop")} (${hhmm(v.read_at)})`,
               health: v.regressions ? "degraded" : "plain" });
  }
  for (const i of s.issues) out.push({ text: i, health: "degraded" });
  for (const u of s.unread) out.push({ text: u, health: "unknown" });
  return out;
}

/** A site's lamp: failing beats degraded beats unknown beats ok. */
export function siteHealth(s: SeoSite): Line["health"] {
  const all = siteLines(s).map((l) => l.health);
  for (const h of ["failing", "degraded", "unknown"] as const) if (all.includes(h)) return h;
  return "ok";
}

/** The New York calendar day of an instant, "YYYY-MM-DD". */
export function nyDay(at: Date | string): string {
  const d = typeof at === "string" ? new Date(at) : at;
  return d.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

/** "Yesterday": readings and reports stamped on the previous New York day. */
export function yesterdayLines(d: SeoPanelData, now: Date): Line[] {
  const day = nyDay(new Date(now.getTime() - 86_400_000));
  const out: Line[] = [];
  for (const k of Object.keys(CHECK_NAMES) as (keyof typeof CHECK_NAMES)[]) {
    const c = d.checks[k];
    if (c?.at && nyDay(c.at) === day) {
      out.push({ text: `${CHECK_NAMES[k]} read ${hhmm(c.at)}`, health: "plain" });
    }
  }
  for (const p of d.reports) {
    if (p.at && nyDay(p.at) === day) {
      out.push({ text: `${p.kind === "academy_gaps" ? "Academy gaps" : `report ${p.slug}`} `
        + `${p.week} stored ${hhmm(p.at)}`, health: "plain" });
    }
  }
  if (out.length === 0) {
    const last = d.reports[0];
    out.push({ text: "nothing recorded yesterday"
      + (last ? `; last report ${last.week}` : "; no report stored yet"), health: "plain" });
  }
  return out;
}

/** "Issues": every site's issues and unread readings, and failed reads. */
export function issueLines(d: SeoPanelData): Line[] {
  const out: Line[] = [];
  for (const s of d.sites) {
    for (const i of s.issues) out.push({ text: `${s.domain || s.slug}: ${i}`, health: "degraded" });
    if (s.search === null || s.vitals === null) {
      out.push({ text: `${s.domain || s.slug}: ${[s.search === null ? "Search Console" : "",
        s.vitals === null ? "speed" : ""].filter(Boolean).join(" and ")} not measured`,
                 health: "unknown" });
    }
  }
  for (const [k, why] of Object.entries(d.errors)) {
    out.push({ text: `${k} not read: ${why}`, health: "unknown" });
  }
  if (out.length === 0) out.push({ text: "no issue found", health: "plain" });
  return out;
}
