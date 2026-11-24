/**
 * The Client Sites tab: its pure model, and the page to static markup (no
 * jsdom). UNKNOWN is never counted as OK, a stale reading is flagged, every
 * row shows its read time, events read newest first.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SitesView } from "./Sites";
import {
  groupSites, isStale, okCount, orderEvents, safeDocUrl, summaryLine,
  type SiteDoc, type SiteRow, type SitesOverview,
} from "./sitesModel";

const NOW = new Date("2026-10-27T10:00:00Z");

function row(over: Partial<SiteRow> = {}): SiteRow {
  return {
    slug: "examplehair", kind: "demo", hostname: "client-site.example.com",
    source: "royal@176c272", managed: true, deployed_at: "2026-10-27T09:00:00Z",
    live_release: "20261027T090000000000Z", live_problem: null,
    health: "ok", health_detail: "up", health_read_at: "2026-10-27T09:45:00Z",
    docs: [], docs_problem: null,
    ...over,
  };
}

function view(sites: SiteRow[]): SitesOverview {
  return { sites, read_at: NOW.toISOString(), check_every_s: 1800 };
}

describe("groupSites", () => {
  it("groups by health and never counts UNKNOWN as OK", () => {
    const g = groupSites(view([
      row(),
      row({ slug: "a", hostname: "agency.example.com", health: "unknown", health_read_at: null }),
      row({ slug: "b", hostname: "joes.example", health: "failing" }),
      row({ slug: "c", hostname: "demo-site.vercel.example", health: "weird" }),
    ]), NOW);
    expect(okCount(g)).toBe(1);
    expect(g.unknown.map((r) => r.slug)).toEqual(["a", "c"]);
    expect(g.failing.map((r) => r.slug)).toEqual(["b"]);
    expect(summaryLine(g)).toContain("2 unknown");
  });

  it("flags a stale reading and moves a stale OK to UNKNOWN", () => {
    const old = row({ health_read_at: "2026-10-27T08:00:00Z" });   // 2 h > 2 x 30 min
    expect(isStale(old, NOW, 1800)).toBe(true);
    expect(isStale(row(), NOW, 1800)).toBe(false);
    const g = groupSites(view([old]), NOW);
    expect(g.ok).toEqual([]);
    expect(g.unknown).toHaveLength(1);
  });

  it("says when nothing is registered", () => {
    expect(summaryLine(groupSites(view([]), NOW))).toBe("No client sites registered.");
  });
});

describe("orderEvents", () => {
  it("reads newest first, undated last", () => {
    const got = orderEvents([
      { at: "2026-10-27T08:00:00Z", event: "deploy_failed", release: null, detail: "" },
      { at: null, event: "rolled_back", release: null, detail: "" },
      { at: "2026-10-27T09:00:00Z", event: "deployed", release: "r", detail: "" },
    ]);
    expect(got.map((e) => e.event)).toEqual(["deployed", "deploy_failed", "rolled_back"]);
  });
});

describe("SitesView", () => {
  it("renders UNKNOWN with its word and hatch, and every read time", () => {
    const html = renderToStaticMarkup(createElement(SitesView, {
      data: view([row(), row({ slug: "x", hostname: "agency.example.com", health: "unknown",
                               health_read_at: null, health_detail: "no reading yet" })]),
      now: NOW,
    }));
    expect(html).toContain("Unknown");
    expect(html).toContain("hatch");
    expect(html).toContain("never read");
    expect(html).toContain("no reading yet");
    expect(html).toContain("client-site.example.com");
    expect(html).toContain("20261027T090000000000Z");
  });
});

const URL_OK = "https://claude.ai/artifact/abc123XYZ";

function doc(over: Partial<SiteDoc> = {}): SiteDoc {
  return {
    id: 7, kind: "brief", title: "examplehair brief", status: "published",
    artifact_url: URL_OK, reported_at: "2026-10-29T10:00:00Z",
    created_at: "2026-10-29T09:00:00Z", handoff_status: "done", ...over,
  };
}

function render(over: Partial<SiteRow>): string {
  return renderToStaticMarkup(createElement(SitesView, { data: view([row(over)]), now: NOW }));
}

describe("safeDocUrl", () => {
  it("takes only a claude.ai artifact link", () => {
    expect(safeDocUrl(URL_OK)).toBe(URL_OK);
    expect(safeDocUrl("https://claude.ai/code/artifact/abc-123_xyz")).not.toBeNull();
    for (const bad of [null, "", "javascript:alert(1)", "http://claude.ai/artifact/abc123",
      "https://claude.ai.evil.com/artifact/abc123", "https://claude.ai/artifact/abc123/../x",
      "https://claude.ai/chat/abc123"]) {
      expect(safeDocUrl(bad)).toBeNull();
    }
  });
});

describe("SiteDocs", () => {
  it("links a published document, labelled as the coding agent's word", () => {
    const html = render({ docs: [doc()], docs_problem: null });
    expect(html).toContain(`href="${URL_OK}"`);
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toContain("examplehair brief");
    expect(html).toContain("not verified");
    expect(html).toContain("read ");
  });

  it("shows a document with no link yet as UNKNOWN, never as a link", () => {
    const html = render({ docs: [doc({ status: "queued", artifact_url: null,
                                       reported_at: null })], docs_problem: null });
    expect(html).not.toContain("href=");
    expect(html).toContain("hatch");
    expect(html).toContain("no Docs link yet");
  });

  it("never renders a link that is not a claude.ai artifact", () => {
    const html = render({ docs: [doc({ artifact_url: "javascript:alert(1)" })],
                          docs_problem: null });
    expect(html).not.toContain("javascript:");
    expect(html).toContain("hatch");
  });

  it("says when the documents could not be read, as UNKNOWN", () => {
    const html = render({ docs: null, docs_problem: "documents could not be read" });
    expect(html).toContain("documents could not be read");
    expect(html).toContain("Unknown");
  });

  it("says when no document has been drafted", () => {
    expect(render({ docs: [], docs_problem: null })).toContain("No Docs artifact yet");
  });
});
