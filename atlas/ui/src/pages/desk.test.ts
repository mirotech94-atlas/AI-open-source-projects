/**
 * T7: the ATLAS Agency sections. Pure mapping and static render:
 * lead status to stage, calendar grouping, Settings booleans, UNKNOWN's
 * treatment, empty states with no placeholder rows, the wordmark and the
 * cmd-K palette over exactly the section ids.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ActivityRow, AgentSummary, ModuleTile } from "../api";
import { AgencyDeskBody, DeskTop, Palette, type DeskProps } from "./AgencyDesk";
import { Decisions } from "./CommandCenter";
import {
  EMPTY, LEAD_STATUSES, SECTIONS, WORDMARK, buildBoard, calendarGroups, connectionRows,
  healthTreatment, leadIdOfSlug, paletteMatches, secretRows, statusStage, teamRows,
  type AcademyCalendar,
} from "./deskModel";
import type { SiteRow } from "./sitesModel";

const READ = "2026-10-27T10:00:00Z";
const NOW = new Date("2026-10-27T10:05:00Z");

const read = <T,>(data: T | null, error: string | null = null) =>
  ({ data, error, fetchedAt: data !== null || error ? new Date(READ) : null });

const lead = (id: number, status: string, business = `Biz ${id}`) =>
  ({ id, business, category: "bakery", status });

const site = (slug: string, deployed_at: string | null): SiteRow => ({
  slug, kind: "built", hostname: null, source: null, managed: true, deployed_at,
  live_release: null, live_problem: null, health: "unknown", health_detail: "",
  health_read_at: null,
});

// ---------------------------------------------------------- the frame

describe("wordmark and palette", () => {
  // Command Center order: attention first, then each agent, then calendar,
  // reports and projects, settings last.
  const IDS = ["decisions", "activity", "team", "sales", "seo", "research", "remediation",
               "calendar", "reports", "clients", "projects", "settings"];

  it("the section ids are exactly the plan's sections", () => {
    expect(SECTIONS.map((s) => s.id)).toEqual(IDS);
  });

  it("an empty query lists exactly the section ids, in page order", () => {
    expect(paletteMatches("").map((s) => s.id)).toEqual(IDS);
    expect(paletteMatches("cal").map((s) => s.id)).toEqual(["calendar"]);
    expect(paletteMatches("zzz")).toEqual([]);
  });

  it("the palette renders one entry per section id and nothing else", () => {
    const h = renderToStaticMarkup(createElement(Palette, {
      open: true, query: "", onQuery: () => {}, onPick: () => {}, onClose: () => {},
    }));
    expect([...h.matchAll(/data-section="([a-z]+)"/g)].map((m) => m[1])).toEqual(IDS);
    expect(renderToStaticMarkup(createElement(Palette, {
      open: false, query: "", onQuery: () => {}, onPick: () => {}, onClose: () => {},
    }))).toBe("");
  });

  it("the header carries the wordmark and an anchor per section", () => {
    expect(WORDMARK).toBe("ATLAS Agency");
    const h = renderToStaticMarkup(createElement(DeskTop, { onPalette: () => {} }));
    expect(h).toContain("ATLAS Agency");
    expect(h).not.toContain("Academy");
    expect([...h.matchAll(/href="#([a-z]+)"/g)].map((m) => m[1])).toEqual(IDS);
  });
});

// ------------------------------------------------------------- board

describe("lead status to stage", () => {
  it("covers every status the schema allows", () => {
    expect(LEAD_STATUSES).toEqual(["new", "audited", "mocked", "contacted", "closed", "dropped"]);
    expect(LEAD_STATUSES.map(statusStage)).toEqual(
      ["prospect", "prospect", "demo", "demo", "closed", "dropped"]);
    expect(statusStage("something-else")).toBeNull();
  });

  it("reads the lead id off a site slug", () => {
    expect(leadIdOfSlug("rosas-bakery-42")).toBe(42);
    expect(leadIdOfSlug("no-id")).toBeNull();
  });

  it("places leads: dropped hidden, closed behind the chip, card -> awaiting, deployed -> live", () => {
    const leads = LEAD_STATUSES.map((s, i) => lead(i + 1, s));
    leads.push(lead(7, "contacted"), lead(8, "audited"), lead(9, "weird"));
    const cards = [{ id: 90, action: "agency.deploy_demo", params: { slug: "biz-7" },
                     expires_at: "2026-10-27T12:00:00Z", status: "pending" },
                   { id: 91, action: "agency.deploy_site", params: { slug: "biz-2" },
                     expires_at: "2026-10-27T09:00:00Z", status: "pending" }];   // expired
    const b = buildBoard(leads, [site("biz-8", "2026-10-20T08:00:00Z"), site("biz-3", null)],
                         cards, NOW.getTime());
    expect(b.columns.prospect.map((c) => c.id)).toEqual([1, 2]);
    expect(b.columns.demo.map((c) => c.id)).toEqual([3, 4]);
    expect(b.columns.awaiting.map((c) => c.id)).toEqual([7]);
    expect(b.columns.awaiting[0]?.cardId).toBe(90);
    expect(b.columns.live.map((c) => c.id)).toEqual([8]);
    expect(b.closed.map((c) => c.id)).toEqual([5]);
    expect(b.dropped).toBe(1);
    expect(b.unsorted).toBe(1);
  });

  it("renders closed only behind a closed (n) chip, and never names a dropped lead", () => {
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props({
      leads: read({ leads: [lead(1, "new"), lead(2, "closed", "Shut Shop"),
                            lead(3, "dropped", "Gone Co")], total: 3 }),
    })));
    expect(h).toContain("closed (1)");
    expect(h).not.toContain("Shut Shop");
    expect(h).not.toContain("Gone Co");
    expect(h).toContain("Biz 1");
  });
});

// ---------------------------------------------------------- calendar

const CAL: AcademyCalendar = {
  as_of: READ, read_at: READ,
  series: [
    { key: "claude", name: "Claude basics", posted: [
      { episode: 1, title: "One", video_id: "a", at: "2026-10-20T17:00:00Z", public: true },
      { episode: 2, title: "Two", video_id: "b", at: "2026-10-21T17:00:00Z", public: false },
    ], next_up: [{ episode: 3, title: "Three" }] },
    { key: "n8n", name: "n8n", posted: [], next_up: [{ episode: 1, title: "Start" }] },
    { key: "quiet", name: "Quiet", posted: [], next_up: [] },
  ],
  next_up: [{ series: "n8n", episode: 1, title: "Start" },
            { series: "claude", episode: 3, title: "Three" }],
  notes: [],
};

describe("calendar grouping", () => {
  it("groups posted rows per series, newest first, and next up with no date", () => {
    const g = calendarGroups(CAL);
    expect(g.map((x) => x.key)).toEqual(["claude", "n8n"]);
    expect(g[0]?.posted.map((p) => p.episode)).toEqual([2, 1]);
    expect(g[0]?.nextUp).toEqual([{ episode: 3, title: "Three" }]);
    for (const x of g) for (const n of x.nextUp) expect(Object.keys(n).sort()).toEqual(["episode", "title"]);
  });

  it("drops a date the server might send for a next-up episode", () => {
    const sneaky = { ...CAL, series: [{ ...CAL.series[1]!,
      next_up: [{ episode: 1, title: "Start", at: "2026-10-28T17:00:00Z" } as never] }] };
    expect(calendarGroups(sneaky)[0]?.nextUp[0]).toEqual({ episode: 1, title: "Start" });
  });

  it("renders next up without any date", () => {
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props({ calendar: read(CAL) })));
    const cal = h.slice(h.indexOf('id="calendar"'), h.indexOf('id="reports"'));
    expect(cal).toContain("next up");
    expect(cal).not.toMatch(/28 Oct/);
    expect(cal).toContain("no date is set");
  });
});

// ------------------------------------------- compact (Miro 2026-10-29)

describe("compact sections", () => {
  it("a board column shows its first 8 leads and counts the rest", () => {
    const leads = Array.from({ length: 20 }, (_, i) => lead(i + 1, "new"));
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props({
      leads: read({ leads, total: 20 }),
    })));
    const col = h.slice(h.indexOf('data-stage="prospect"'));
    const ul = col.slice(0, col.indexOf("</ul>"));
    expect(ul.match(/data-lead=/g)).toHaveLength(8);
    expect(ul).toContain("+12 more");
  });
});

// ---------------------------------------------------------- settings

describe("settings", () => {
  it("shows only set / unset booleans, never a value", () => {
    const rows = secretRows({ SLACK_BOT_TOKEN: true, SLACK_APP_TOKEN: false,
                              ODD: "xoxb-secret-value" });
    expect(rows).toEqual([
      { name: "SLACK_BOT_TOKEN", state: "set" },
      { name: "SLACK_APP_TOKEN", state: "unset" },
      { name: "ODD", state: "unknown" },
    ]);
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props({
      secrets: read({ secrets: { SLACK_BOT_TOKEN: true, SLACK_APP_TOKEN: false,
                                 ODD: "xoxb-secret-value" } }),
    })));
    expect(h).not.toContain("xoxb-secret-value");
    expect(h).toContain("SLACK_APP_TOKEN");
    expect(h).toContain("not set");
  });

  it("lists slack.socket, and a missing one is UNKNOWN", () => {
    const slack = mod("slack", [{ name: "bot", health: "ok" }]);
    const rows = connectionRows([slack]);
    expect(rows.map((r) => r.module)).toEqual(["youtube", "slack", "mail", "calendar"]);
    const s = rows.find((r) => r.module === "slack")!;
    expect(s.checks.find((c) => c.name === "socket")?.health).toBe("unknown");
    expect(rows.find((r) => r.module === "mail")?.health).toBe("unknown");
    const withSocket = connectionRows([mod("slack", [{ name: "socket", health: "failing" }])]);
    expect(withSocket[1]?.checks).toHaveLength(1);
    expect(withSocket[1]?.checks[0]?.health).toBe("failing");
  });
});

// ------------------------------------------------------------ UNKNOWN

describe("UNKNOWN treatment", () => {
  it("is the grey-dashed class with the word, never OK's", () => {
    const u = healthTreatment("unknown");
    const ok = healthTreatment("ok");
    expect(u.className).toContain("desk-h-unknown");
    expect(u.word).toBe("UNKNOWN");
    expect(ok.className).not.toContain("desk-h-unknown");
    expect(u.className).not.toBe(ok.className);
    for (const h of ["ok", "degraded", "failing"] as const) {
      expect(healthTreatment(h).className).not.toContain("unknown");
    }
  });

  it("an unreadable overview renders every connection UNKNOWN in that class", () => {
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props({
      overview: read({ as_of: READ, modules: [], pending_approvals: 0, watch_only: true }),
    })));
    const settings = h.slice(h.indexOf('id="settings"'));
    expect(settings.match(/class="desk-h desk-h-unknown"[^>]*>UNKNOWN</g)?.length)
      .toBeGreaterThanOrEqual(4);
  });

  it("a failed read says UNKNOWN with the reason", () => {
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props({
      calendar: read<AcademyCalendar>(null, "HTTP 503"),
    })));
    const cal = h.slice(h.indexOf('id="calendar"'), h.indexOf('id="reports"'));
    expect(cal).toContain("Unknown");
    expect(cal).toContain("HTTP 503");
    expect(cal).toContain("read failed");
  });
});

// ------------------------------------------------------- empty states

describe("empty states", () => {
  const empty = (): Partial<DeskProps> => ({
    leads: read({ leads: [], total: 0 }),
    approvals: read({ as_of: READ, watch_only: true, proposals: [] }),
    calendar: read({ ...CAL, series: [], next_up: [] }),
    activity: read({ activity: [] }),
    reports: read({ reports: [] }),
  });

  it("say what is missing and draw no placeholder row", () => {
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props(empty())));
    for (const id of ["clients", "calendar", "reports"] as const) {
      const start = h.indexOf(`id="${id}"`);
      const end = h.indexOf("</section>", start);
      const sec = h.slice(start, end);
      expect(sec).toContain(EMPTY[id]);
      expect(sec).not.toContain("<li");
    }
  });

  it("a section not read yet shows a skeleton, not rows or a number", () => {
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props({})));
    const start = h.indexOf('id="clients"');
    const sec = h.slice(start, h.indexOf("</section>", start));
    expect(sec).toContain("desk-skel");
    expect(sec).not.toContain("<li");
    expect(sec).toContain("not read yet");
  });

  it("keeps the Slack line; the desk itself offers no control that decides", () => {
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props(empty())));
    const top = renderToStaticMarkup(createElement(Decisions, {
      approvals: { data: { as_of: READ, watch_only: true, proposals: [] }, error: null,
                   fetchedAt: new Date(READ), loading: false, reload: () => undefined } as never,
      now: Date.parse(READ) }));
    expect(top).toContain("in Slack (#agency-decisions); silence");
    expect(top).not.toMatch(/approve<\/button>|decline<\/button>|<form/i);
    expect(h).not.toMatch(/approve<\/button>|deny<\/button>|decide/i);
    expect(h).not.toContain("<form");
  });

  // Miro 2026-10-29: GUI decisions. Buttons only on a pending Tier 2 card,
  // and Approve says it has no effect while watch-only.
  it("Needs you offers Decline/Approve only on a pending Tier 2 card", () => {
    const card = (id: number, tier: number) => ({
      id, action: "agency.send_pitch", params: {}, reason: "", tier, tier_reason: "",
      status: "pending", confidence: null, rollback_plan: "", evidence: {},
      created_at: READ, expires_at: new Date(Date.parse(READ) + 3_600_000).toISOString(),
      dry_run: null, dry_run_ok: null, dry_run_at: null, finding: null,
    });
    const fresh = (data: unknown) => ({ data, error: null, fetchedAt: new Date(READ),
                                         loading: false, reload: () => undefined }) as never;
    const top = renderToStaticMarkup(createElement(Decisions, {
      approvals: fresh({ as_of: READ, watch_only: true, proposals: [card(41, 2), card(42, 1)] }),
      session: fresh({ as_of: READ, authenticated: true, user: "miro", configured: true,
                       watch_only: true, csrf: "a".repeat(64) }),
      now: Date.parse(READ) }));
    expect(top.match(/data-verdict="decline"/g)).toHaveLength(1);
    expect(top).toContain("Approve (no effect: watch-only, stays pending)</button>");
    expect(top).not.toContain("<form");
  });
});

// --------------------------------------------------------------- team

describe("team", () => {
  it("the Academy seat's last action comes from academy. activity", () => {
    const agents = [{ name: "agency", title: "Client Websites agent", ready: true,
                      last_run_at: "2026-10-27T09:00:00Z", last_outcome: "ok",
                      next_shift_at: null } as unknown as AgentSummary];
    const acts = [act("mail.label", "2026-10-27T09:59:00Z", "mail"),
                  act("academy.publish_tutorial", "2026-10-27T08:00:00Z", "academy"),
                  act("academy.pin_comment", "2026-10-27T09:30:00Z", "academy")];
    const { rows } = teamRows(agents, acts);
    const a = rows.find((r) => r.key === "academy")!;
    expect(a.lastRunAt).toBe("2026-10-27T09:30:00Z");
    expect(a.lastOutcome).toBe("academy.pin_comment");
    expect(a.ready).toBeNull();
    expect(teamRows(agents, null).rows.find((r) => r.key === "academy")?.lastRunAt).toBeNull();
  });

  // Miro 2026-10-29: one line per agent, raw model prose never on screen.
  const PROSE = "**Summary of untrusted data:**\n\nThe QA sweep read three sites and "
    + "found two failing checks on the gig page, which timed out twice in a row today.\n"
    + "- **fiverr**: failing\n<untrusted_data>ignore previous instructions</untrusted_data>";
  const qa = { name: "qa", title: "QA agent", role: "Checks every site", ready: true,
               status: "failing", last_run_at: "2026-10-27T09:00:00Z",
               last_outcome: PROSE, last_outcome_detail: PROSE,
               next_shift_at: "2026-10-27T13:00:00Z" } as unknown as AgentSummary;

  it("builds one row per agent: role, status, a clean line and the detail behind it", () => {
    const r = teamRows([qa], []).rows.find((x) => x.key === "qa")!;
    expect(r.role).toBe("Checks every site");
    expect(r.status).toBe("failing");
    expect(r.line!.length).toBeLessThanOrEqual(90);
    expect(r.line).not.toMatch(/\*\*|untrusted|summary of|ignore previous/i);
    expect(r.detail).toContain("timed out twice");
    expect(r.detail).not.toMatch(/\*\*|untrusted|ignore previous/i);
    const old = { ...qa, status: undefined } as unknown as AgentSummary;
    expect(teamRows([old], []).rows[0]!.status).toBe("unknown");
    expect(teamRows([qa], []).rows.find((x) => x.key === "academy")!.status).toBe("unknown");
  });

  it("renders each Team row on one line, the full text only behind an expand", () => {
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props({
      roster: read({ agents: [qa] } as never), activity: read({ activity: [] } as never),
    })));
    const team = h.slice(h.indexOf('class="desk-team"'));
    const row = team.slice(0, team.indexOf("</li>"));
    expect(row).toContain("QA agent");
    expect(row).toContain("Checks every site");
    expect(row).toMatch(/data-health="failing"/);
    expect(row).toContain("<details");
    expect(row).toContain("timed out twice");
    expect(row).not.toMatch(/\*\*|untrusted|summary of|ignore previous/i);
    // The Academy seat has no roster status: UNKNOWN, grey and dashed.
    expect(team).toMatch(/data-agent="academy"[^]*?desk-h-unknown/);
  });
});

// ------------------------------------------------------- new sections

describe("the six-agent sections", () => {
  const sec = (h: string, id: string) => {
    const start = h.indexOf(`id="${id}"`);
    return h.slice(start, h.indexOf("</section>", start));
  };

  it("each new section has its own empty copy and draws no placeholder row", () => {
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props({
      leads: read({ leads: [], total: 0 }),
      approvals: read({ as_of: READ, watch_only: true, proposals: [] }),
      projects: read({ served_at: READ, projects: [] }),
      remediation: read({ served_at: READ, open: 0, tasks: [] }),
      seo: read({ served_at: READ, rows: [] }),
      research: read({ served_at: READ, rows: [] }),
    })));
    for (const id of ["projects", "sales", "seo", "research", "remediation"] as const) {
      expect(EMPTY[id].length, id).toBeGreaterThan(10);
      expect(sec(h, id), id).toContain(EMPTY[id]);
      expect(sec(h, id), id).not.toContain("<li");
    }
  });

  it("not measured and UNKNOWN are different words in different treatments", () => {
    const nm = renderToStaticMarkup(createElement(AgencyDeskBody, props({})));
    const seo = sec(nm, "seo");
    expect(seo).toContain("Not measured");
    expect(seo).toContain("desk-nm");
    expect(seo).not.toContain("desk-h-unknown");
    const unk = renderToStaticMarkup(createElement(AgencyDeskBody, props({
      seo: read(null, "HTTP 503"), research: read(null, "HTTP 503"),
    })));
    expect(sec(unk, "seo")).toContain("Unknown");
    expect(sec(unk, "seo")).toContain("HTTP 503");
    expect(sec(unk, "seo")).not.toContain("Not measured");
    expect(sec(unk, "research")).toContain("HTTP 503");
  });

  it("no number renders without its read time", () => {
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props({
      seo: read({ served_at: READ, rows: [
        { label: "Lighthouse agency.storefront.test/preview/joes", value: 91, unit: "", read_at: READ },
        { label: "Rank bakery newark", value: 4, unit: "", read_at: null },
      ] }),
    })));
    const seo = sec(h, "seo");
    expect(seo).toMatch(/91[^<]*<\/b>\s*<small>read \d\d:\d\d/);
    expect(seo).not.toMatch(/>4</);
    expect(seo).toContain("not measured");
  });

  it("projects are listed per client with open task counts and a read time", () => {
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props({
      projects: read({ served_at: READ, projects: [
        { id: 1, client_lead_id: 7, client_name: "Joe's Bakery", name: "Website", status: "active",
          kickoff_at: null, created_at: null, updated_at: READ, open_tasks: 3 },
      ] }),
    })));
    const p = sec(h, "projects");
    expect(p).toContain("Joe&#x27;s Bakery");
    expect(p).toContain("Website");
    expect(p).toMatch(/3 open/);
    expect(p).toMatch(/read \d\d:\d\d/);
  });

  it("remediation lists open tasks with owner, fix and source; a failed read is UNKNOWN", () => {
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props({
      remediation: read({ served_at: READ, open: 1, tasks: [
        { id: 4, project_id: null, title: "Title too long", owner: "seo", status: "todo",
          evidence: null, suggested_fix: "Cut it to 60 characters", source_action: "qa.check_site",
          source_ref: "site:joes", due_at: null, created_at: READ, done_at: null, open: true },
      ] }),
    })));
    const r = sec(h, "remediation");
    expect(r).toContain("Title too long");
    expect(r).toContain("seo");
    expect(r).toContain("Cut it to 60 characters");
    expect(r).toContain("qa.check_site");
    const bad = renderToStaticMarkup(createElement(AgencyDeskBody, props({
      remediation: read(null, "HTTP 503") })));
    expect(sec(bad, "remediation")).toContain("HTTP 503");
    expect(sec(bad, "remediation")).toContain("Unknown");
  });

  it("sales counts leads by stage and the open sales cards, each stamped", () => {
    const h = renderToStaticMarkup(createElement(AgencyDeskBody, props({
      leads: read({ leads: [lead(1, "new"), lead(2, "contacted"), lead(3, "contacted")], total: 3 }),
      approvals: read({ as_of: READ, watch_only: true, proposals: [
        { id: 9, action: "agency.send_outreach", tier: 2, expires_at: "2026-10-27T20:00:00Z" },
        { id: 10, action: "academy.publish_tutorial", tier: 2, expires_at: "2026-10-27T20:00:00Z" },
      ] } as never),
    })));
    const s = sec(h, "sales");
    expect(s).toMatch(/Prospect[^]*1/);
    expect(s).toMatch(/Demo[^]*2/);
    expect(s).toMatch(/Open sales cards[^]*>1</);
    expect(s).toMatch(/read \d\d:\d\d/);
    expect(s).toContain("Follow-ups due");
  });

  it("sales reads follow-ups due and cards left from its checks, UNKNOWN never a number", () => {
    const check = (name: string, health: string, value: number | null) => ({
      name, health, detail: "", observed_at: READ, next_run_at: null, interval_s: 1800,
      value, unit: null, read_at: READ });
    const tile = (checks: unknown[]) => read({ as_of: READ, pending_approvals: 0,
      watch_only: true, modules: [{ module: "sales", title: "Sales", health: "ok", checks,
        last_check_at: READ, next_check_at: null, enabled: true, latest_finding: null }] } as never);
    const leads = read({ leads: [lead(1, "new")], total: 1 });
    const ok = sec(renderToStaticMarkup(createElement(AgencyDeskBody, props({ leads,
      overview: tile([check("followups_due", "ok", 2), check("cap_left", "ok", 7)]) }))),
      "sales");
    expect(ok).toMatch(/Follow-ups due[^]*<b>2<\/b>[^]*read \d\d:\d\d/);
    expect(ok).toMatch(/cards left today[^]*<b>7<\/b>/);
    const unk = sec(renderToStaticMarkup(createElement(AgencyDeskBody, props({ leads,
      overview: tile([check("followups_due", "unknown", 2)]) }))), "sales");
    expect(unk).toMatch(/Follow-ups due[^]*data-health="unknown">UNKNOWN/);
    expect(unk).not.toMatch(/Follow-ups due<\/dt><dd><b>2/);
    expect(unk).toMatch(/cards left today[^]*not measured/);
  });

  it("the Decisions panel shows each card's tag, and the desk does not repeat it", () => {
    const approvals = read({ as_of: READ, watch_only: true, proposals: [
      { id: 9, action: "agency.send_outreach", tier: 2, expires_at: "2099-10-27T20:00:00Z",
        params: { business: "Joe's Bakery" } },
    ] } as never);
    const top = renderToStaticMarkup(createElement(Decisions, {
      approvals: { ...approvals, loading: false, reload: () => undefined } as never,
      now: Date.parse(READ) }));
    expect(top).toContain('id="decisions"');
    expect(top).toContain("Websites · Joe&#x27;s Bakery");
    const desk = renderToStaticMarkup(createElement(AgencyDeskBody, props({ approvals })));
    expect(desk).not.toContain('id="decisions"');
    expect(desk).not.toContain('id="activity"');
  });
});

// ------------------------------------------------------------ helpers

function mod(module: string, checks: { name: string; health: string }[]): ModuleTile {
  return {
    module, title: module, health: checks[0]?.health ?? "unknown",
    checks: checks.map((c) => ({ ...c, detail: "", observed_at: READ, next_run_at: null,
                                 interval_s: 60 })),
    last_check_at: READ, next_check_at: null, enabled: true, latest_finding: null,
  };
}

function act(subject: string, at: string, module: string): ActivityRow {
  return { kind: "action", at, module, subject, health: null, detail: "", value: null,
           unit: null, id: null };
}

function props(over: Partial<DeskProps>): DeskProps {
  const none = <T,>() => read<T>(null);
  return {
    overview: none(), leads: none(), sites: none(), approvals: none(), calendar: none(),
    activity: none(), reports: none(), roster: none(), secrets: none(), projects: none(),
    remediation: none(), now: NOW,
    ...over,
  };
}
