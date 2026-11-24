import { describe, expect, it } from "vitest";
import type { AgencyActivity, AgentActivity, ModuleTile, SeoPanelData } from "../api";
import {
  alarmLines, healthLines, moneyFigures, nextUp, searchLines, siteLines, todayLines,
} from "./commandSections";
import type { WeekDay } from "./homeModel";
import type { SitesOverview } from "./sitesModel";

const AT = "2026-10-29T09:00:00Z";

function agent(name: string, over: Partial<AgentActivity["now"]> = {}): AgentActivity {
  return {
    name, label: name.toUpperCase(), title: name, modules: [],
    now: { read_at: AT, headline: null, in_flight: 0, waiting: 0, cards: [], running_jobs: [],
           last_run: { at: AT, failed: false }, last_run_known: true, unread: [], ...over },
    yesterday: { read_at: AT, day: "2026-10-28", from: AT, to: AT, runs: null, failed_runs: null,
                 last_run_at: null, actions_ok: null, actions_failed: null, last_actions: [],
                 cards_expired: null, unread: [] },
    issues: { read_at: AT, count: 0, items: [], unread: [] },
  };
}

describe("todayLines", () => {
  it("always lists the nine roster agents; a missing one is Unknown", () => {
    const read: AgencyActivity = { served_at: AT, read_at: AT, errors: {},
      agents: [agent("sites", { headline: "Deployed shop v3" })] };
    const lines = todayLines(read);
    expect(lines).toHaveLength(9);
    expect(lines.find((l) => l.name === "sites")?.text).toBe("Deployed shop v3");
    const qa = lines.find((l) => l.name === "qa");
    expect(qa?.text).toBeNull();
    expect(qa?.health).toBe("unknown");
    expect(qa?.runs).toBeNull();
    const tutorco = lines.find((l) => l.name === "tutorco");
    expect(tutorco?.health).toBe("unknown");
    expect(tutorco?.text).toBeNull();
  });
  it("an unread read leaves every agent Unknown, never 0 runs", () => {
    for (const l of todayLines(null)) {
      expect(l.health).toBe("unknown");
      expect(l.runs).toBeNull();
    }
  });
  it("a failed last run is failing; an unknown last run is not called OK", () => {
    const read: AgencyActivity = { served_at: AT, read_at: AT, errors: {}, agents: [
      agent("seo", { last_run: { at: AT, failed: true } }),
      agent("qa", { last_run: null, last_run_known: false }),
    ] };
    const lines = todayLines(read);
    expect(lines.find((l) => l.name === "seo")?.health).toBe("failing");
    expect(lines.find((l) => l.name === "qa")?.health).toBe("unknown");
  });
});

const tile = (module: string, health: string, checks: ModuleTile["checks"]): ModuleTile => ({
  module, title: module, health, checks, last_check_at: AT, next_check_at: null, enabled: true,
  latest_finding: null,
});
const check = (name: string, health: string, over: Partial<ModuleTile["checks"][number]> = {}) => ({
  name, health, detail: `${name} detail`, observed_at: AT, next_run_at: null, interval_s: 3600,
  ...over,
});

describe("moneyFigures", () => {
  it("shows a check's own value, and Unknown when unread or stale", () => {
    const mods = [tile("shop", "ok", [
      check("sales_7d", "ok", { value: 42.5, unit: "usd" }),
      check("sales_count_7d", "ok", { value: 3, unit: "sales", stale: true }),
    ])];
    const f = moneyFigures(mods, null);
    const rev = f.in.find((p) => p.check === "shop.sales_7d");
    expect(rev?.value).toBe(42.5);
    expect(rev?.readAtIso).toBe(AT);
    expect(f.in.find((p) => p.check === "shop.sales_count_7d")?.state).toBe("unknown");
    expect(f.out.every((p) => p.state === "unknown")).toBe(true);
  });
  it("a failed overview read is Unknown everywhere", () => {
    const f = moneyFigures(null, "HTTP 500");
    expect([...f.in, ...f.out].every((p) => p.state === "unknown" && p.value === null)).toBe(true);
  });
});

describe("healthLines", () => {
  it("lists only what is not OK, worst first, and a stale check is Unknown", () => {
    const lines = healthLines([
      tile("seo", "ok", [check("search_console", "ok")]),
      tile("qa", "unknown", [check("smoke", "ok", { stale: true })]),
      tile("shop", "failing", [check("sales_7d", "failing")]),
    ]);
    expect(lines.map((l) => l.module)).toEqual(["shop", "qa"]);
    expect(lines[1]?.why).toContain("reading too old");
  });
});

describe("search and sites", () => {
  it("a null search figure stays null (Unknown), not 0", () => {
    const seo = { served_at: AT, read_at: AT, errors: {}, reports: [],
      checks: { search_console: null, core_web_vitals: null },
      sites: [{ slug: "a", domain: "a.com", search: null, issues: [], unread: [],
                vitals: { mobile: 91, desktop: null, mobile_lcp_ms: null, regressions: null,
                          read_at: AT } }] } as SeoPanelData;
    const [l] = searchLines(seo);
    expect(l?.clicks).toBeNull();
    expect(l?.mobile).toBe(91);
    expect(l?.desktop).toBeNull();
  });
  it("a stale OK site is Unknown; a draft doc is listed with no link (Unknown), never hidden", () => {
    const view: SitesOverview = { read_at: AT, check_every_s: 60, sites: [{
      slug: "a", kind: "built", hostname: "a.com", source: null, managed: true, deployed_at: null,
      live_release: null, live_problem: null, health: "ok", health_detail: "",
      health_read_at: "2026-10-29T08:00:00Z", docs_problem: null,
      docs: [
        { id: 1, kind: "brief", title: "Brief", status: "published", artifact_url: "https://claude.ai/artifact/abc123def",
          reported_at: null, created_at: null, handoff_status: null },
        { id: 2, kind: "report", title: "Draft", status: "draft", artifact_url: null,
          reported_at: null, created_at: null, handoff_status: null },
      ] }] };
    const [s] = siteLines(view, new Date("2026-10-29T09:00:00Z"));
    expect(s?.health).toBe("unknown");
    expect(s?.docs?.map((d) => [d.id, d.url])).toEqual([[1, "https://claude.ai/artifact/abc123def"], [2, null]]);
  });
  it("a published doc whose link is not a claude.ai artifact gets no link", () => {
    const view: SitesOverview = { read_at: AT, check_every_s: 60, sites: [{
      slug: "b", kind: "built", hostname: "b.com", source: null, managed: true, deployed_at: null,
      live_release: null, live_problem: null, health: "ok", health_detail: "",
      health_read_at: AT, docs_problem: null,
      docs: [{ id: 3, kind: "brief", title: "Bad", status: "published",
               artifact_url: "javascript:alert(1)", reported_at: null, created_at: null,
               handoff_status: null }] }] };
    expect(siteLines(view, new Date(AT))[0]?.docs?.[0]?.url).toBeNull();
  });
});

describe("alarmLines (the compact Health panel)", () => {
  it("lists only FAILING and DEGRADED; UNKNOWN is left to the count", () => {
    const lines = alarmLines([
      tile("seo", "ok", [check("search_console", "ok")]),
      tile("qa", "unknown", [check("smoke", "unknown")]),
      tile("mail", "degraded", [check("inbox", "degraded")]),
      tile("shop", "failing", [check("sales_7d", "failing")]),
    ]);
    expect(lines.map((l) => [l.module, l.health])).toEqual([
      ["shop", "failing"], ["mail", "degraded"],
    ]);
    expect(lines.every((l) => l.readAt === AT)).toBe(true);
  });

  it("an unread overview lists nothing (the panel says Unknown instead)", () => {
    expect(alarmLines(null)).toEqual([]);
  });
});

describe("nextUp (Coming up, one line)", () => {
  const day = (iso: string, items: [string, string][]): WeekDay => ({
    date: new Date(iso), key: iso, isToday: false, diary: "read", until: null, unread: [],
    items: items.map(([at, title]) => ({ at: new Date(at), title, kind: "event" as const,
                                          detail: "", source: "calendar" })),
  });

  it("is the next three items from now, soonest first, past ones dropped", () => {
    const now = new Date("2026-10-29T12:00:00Z");
    const days = [
      day("2026-10-29T00:00:00Z", [["2026-10-29T09:00:00Z", "past"],
                                   ["2026-10-29T15:00:00Z", "b"]]),
      day("2026-10-30T00:00:00Z", [["2026-10-30T08:00:00Z", "c"],
                                   ["2026-10-30T07:00:00Z", "c0"],
                                   ["2026-10-30T09:00:00Z", "d"]]),
    ];
    expect(nextUp(days, now).map((i) => i.title)).toEqual(["b", "c0", "c"]);
    expect(nextUp(days, now, 1).map((i) => i.title)).toEqual(["b"]);
  });
});
