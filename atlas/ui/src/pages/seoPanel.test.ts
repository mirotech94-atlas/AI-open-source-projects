import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { SeoPanelData, SeoSite } from "../api";
import type { Read } from "./homeModel";
import { SeoPanelBody } from "./SeoPanel";
import {
  checkLines, issueLines, siteHealth, siteLines, yesterdayLines,
} from "./seoPanelModel";

const AT = "2026-10-29T09:00:00+00:00";

function site(over: Partial<SeoSite> = {}): SeoSite {
  return {
    slug: "joes", domain: "joes.example",
    search: { clicks: 80, impressions: 2000, ctr: 0.04, position: 9.5, striking: 2,
              losing: 0, read_at: AT, range: { start: "2026-10-01", end: "2026-10-26" } },
    vitals: { mobile: 88, desktop: 97, mobile_lcp_ms: 2100, regressions: 0, read_at: AT },
    issues: [], unread: [], ...over,
  };
}

const data = (sites: SeoSite[], over: Partial<SeoPanelData> = {}): SeoPanelData => ({
  served_at: AT, read_at: AT,
  checks: { search_console: { health: "ok", at: AT, why: null },
            core_web_vitals: { health: "ok", at: AT, why: null } },
  sites, reports: [{ slug: "joes", kind: "client_weekly", week: "2026-W44", at: AT }],
  errors: {}, ...over,
});
const ok = <T,>(d: T): Read<T> => ({ data: d, error: null, fetchedAt: new Date(AT) });
const render = (r: Read<SeoPanelData>): string =>
  renderToStaticMarkup(createElement(SeoPanelBody, { seo: r }));

describe("SEO panel", () => {
  it("shows clicks, position, speed and counts with their read times", () => {
    const lines = siteLines(site()).map((l) => l.text).join(" | ");
    expect(lines).toContain("80 clicks");
    expect(lines).toContain("position 9.5");
    expect(lines).toContain("2 striking-distance queries");
    expect(lines).toContain("mobile 88");
    expect(siteHealth(site())).toBe("ok");
    const html = render(ok(data([site()])));
    expect(html).toContain('data-site="joes"');
    expect(html).toMatch(/read \d\d:\d\d/);
    expect(html).toContain("2026-W44");
  });

  it("a site with no reading is UNKNOWN, grey and dashed, never zero", () => {
    const s = site({ search: null, vitals: null,
                     unread: ["Search Console agency.storefront.test is not verified"] });
    expect(siteHealth(s)).toBe("unknown");
    const lines = siteLines(s);
    expect(lines.some((l) => l.health === "unknown" && l.text.includes("not measured"))).toBe(true);
    expect(lines.map((l) => l.text).join(" ")).not.toMatch(/\b0 clicks/);
    const html = render(ok(data([s])));
    expect(html).toContain('data-health="unknown"');
    expect(html).toContain("cmd-word-unknown");
  });

  it("issues and regressions are degraded", () => {
    const s = site({ issues: ["speed regression: mobile score 70 -> 41"],
                     vitals: { mobile: 41, desktop: 90, mobile_lcp_ms: 5200, regressions: 1,
                               read_at: AT } });
    expect(siteHealth(s)).toBe("degraded");
  });

  it("a check that never ran or failed to read is unknown with the reason", () => {
    const lines = checkLines(data([], {
      checks: { search_console: { health: "unknown", at: AT,
                                  why: "rerun ./bootstrap/google-login.sh --reconsent" },
                core_web_vitals: null } }));
    expect(lines[0]?.health).toBe("unknown");
    expect(lines[0]?.text).toContain("--reconsent");
    expect(lines[1]?.health).toBe("unknown");
    expect(lines[1]?.text).toContain("not read yet");
  });

  it("a failed read of the panel is UNKNOWN with the reason", () => {
    const html = render({ data: null, error: "HTTP 503", fetchedAt: new Date(AT) });
    expect(html).toContain("cmd-word-unknown");
    expect(html).toContain("HTTP 503");
  });

  it("the agent card has now, yesterday and issues", () => {
    const d = data([site({ search: null, issues: ["no sitemap submitted"] })], {
      reports: [{ slug: "joes", kind: "client_weekly", week: "2026-W44",
                  at: "2026-10-28T15:00:00+00:00" }] });
    const now = new Date("2026-10-29T16:00:00+00:00");
    expect(yesterdayLines(d, now).map((l) => l.text).join(" ")).toContain("report joes 2026-W44");
    const issues = issueLines(d);
    expect(issues.some((l) => l.text.includes("no sitemap submitted"))).toBe(true);
    expect(issues.some((l) => l.health === "unknown" && l.text.includes("Search Console not measured")))
      .toBe(true);
    const html = renderToStaticMarkup(createElement(SeoPanelBody, { seo: ok(d), now }));
    for (const h of ["Now", "Yesterday", "Issues"]) expect(html).toContain(`<h4>${h}`);
  });
});
