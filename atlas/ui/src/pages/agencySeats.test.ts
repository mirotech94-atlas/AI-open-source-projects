/**
 * The Agency tab: eight seats, each with a brief, its own checks, what it has
 * finished, and what waits for an answer. Unknown is never ok; retired lanes
 * never show.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ActivityRow, AgentSummary, CheckSummary, ModuleTile } from "../api";
import { AgencyBody } from "./AgencySeatCards";
import {
  SEATS, doneSoFar, seatChecks, seatHealth, seatLastAction, seatRemediation, seatRoster,
} from "./agencySeats";
import type { RemediationTask } from "./remediationModel";

const READ = "2026-10-27T09:15:00Z";

function check(name: string, over: Partial<CheckSummary> = {}): CheckSummary {
  return { name, health: "ok", detail: "", observed_at: READ, next_run_at: null,
           interval_s: 3600, stale: false, state: null, ...over };
}
function mod(module: string, checks: CheckSummary[], over: Partial<ModuleTile> = {}): ModuleTile {
  return { module, title: module, health: "ok", checks, last_check_at: READ,
           next_check_at: null, enabled: true, latest_finding: null, setup: null, ...over };
}
const seat = (key: string) => SEATS.find((s) => s.key === key)!;

function action(subject: string, detail: string, at: string): ActivityRow {
  return { kind: "action", at, module: subject.split(".")[0] ?? "", subject, health: null,
           detail, value: null, unit: null, id: null };
}
function agent(name: string, over: Partial<AgentSummary> = {}): AgentSummary {
  return { name, title: name, display: name, runs_24h: 0, last_run_at: null, ready: true,
           ...over } as unknown as AgentSummary;
}

const KEYS = ["websites", "personal", "academy", "sales", "bizops", "seo", "research", "qa"];

describe("SEATS", () => {
  it("has eight seats, in order, with static text and a role each", () => {
    expect(SEATS.map((s) => s.key)).toEqual(KEYS);
    expect(SEATS.map((s) => s.title)).toEqual([
      "Client Websites", "Personal Ops", "Content", "Sales & Lead Gen", "Business Automation",
      "SEO & Growth", "Research & Intel", "QA"]);
    for (const s of SEATS) {
      expect(s.brief.length, s.key).toBeGreaterThan(20);
      expect(s.role.length, s.key).toBeGreaterThan(3);
    }
    expect(seat("websites").checks).toEqual(["agency.*"]);
    expect(seat("personal").checks).toEqual(["mail.*", "calendar.*"]);
    expect(seat("academy").checks).toEqual(
      ["academy.*", "youtube.clips_*", "youtube.amsm_*", "community.*", "reddit.*"]);
    expect(seat("sales").checks).toEqual(["sales.*"]);
    expect(seat("bizops").checks).toEqual(["bizops.*"]);
    expect(seat("seo").checks).toEqual(["seo.*"]);
    expect(seat("research").checks).toEqual(["research.*"]);
    expect(seat("qa").checks).toEqual(["qa.*"]);
    expect(seat("websites").actionPrefix).toEqual(["agency."]);
    expect(seat("personal").actionPrefix).toEqual(["mail.", "calendar."]);
    expect(seat("academy").actionPrefix).toEqual(["academy.", "youtube.", "community.", "reddit."]);
    expect(seat("websites").rosterAgents).toEqual(["sites"]);
    expect(seat("personal").rosterAgents).toEqual(["inbox"]);
    expect(seat("academy").rosterAgents).toEqual(["content"]);
    for (const k of ["sales", "bizops", "seo", "research", "qa"]) {
      expect(seat(k).rosterAgents).toEqual([k]);
    }
    for (const s of SEATS) expect(["#agency-decisions", "#agency-alerts"]).toContain(s.slack);
  });
});

const good = [
  mod("agency", [check("leads_waiting")]),
  mod("mail", [check("watch_active")]), mod("calendar", [check("conflicts")]),
  mod("academy", [check("delivery")]),
  mod("youtube", [check("clips_stats"), check("clips_token"), check("amsm_stats")]),
  mod("community", [check("queue")]), mod("reddit", [check("account")]),
  mod("sales", [check("pipeline")]), mod("bizops", [check("invoices")]),
  mod("seo", [check("audits")]), mod("research", [check("briefs")]),
  mod("qa", [check("gate_health"), check("open_remediation")]),
];

describe("seatHealth", () => {
  it("is ok only when every check of the seat reads ok", () => {
    for (const s of SEATS) expect(seatHealth(s, good), s.key).toBe("ok");
  });

  it("a seat whose module is not registered is unknown", () => {
    for (const m of ["sales", "bizops", "seo", "research", "qa", "reddit", "community"]) {
      const without = good.filter((t) => t.module !== m);
      const owner = m === "reddit" || m === "community" ? "academy" : m;
      expect(seatHealth(seat(owner), without), m).toBe("unknown");
    }
  });

  it("the Content seat reads only its own patterns", () => {
    const others = good.map((t) => t.module === "sales" || t.module === "agency"
      ? { ...t, checks: t.checks.map((c) => ({ ...c, health: "failing" })) } : t);
    expect(seatHealth(seat("academy"), others)).toBe("ok");
    const yt = mod("youtube", [check("clips_stats"), check("amsm_stats"),
                               check("flycheap_stats", { health: "failing" })]);
    expect(seatHealth(seat("academy"), [...good.filter((t) => t.module !== "youtube"), yt]))
      .toBe("ok");
    const community = mod("community", [check("queue", { health: "failing" })]);
    expect(seatHealth(seat("academy"), [...good.filter((t) => t.module !== "community"), community]))
      .toBe("failing");
  });

  it("is unknown when unread, a module is missing, set aside, or a check is stale", () => {
    expect(seatHealth(seat("websites"), null)).toBe("unknown");
    expect(seatHealth(seat("personal"), good.filter((m) => m.module !== "calendar"))).toBe("unknown");
    expect(seatHealth(seat("websites"), [mod("agency", [check("x")], { setup: "not set up" })]))
      .toBe("unknown");
    expect(seatHealth(seat("websites"), [mod("agency", [check("x")], { enabled: false })]))
      .toBe("unknown");
    expect(seatHealth(seat("websites"), [mod("agency", [check("x"), check("y", { stale: true })])]))
      .toBe("unknown");
  });

  it("an unknown check keeps the seat from ok; trouble outranks unknown", () => {
    const withUnknown = [mod("agency", [check("a"), check("b", { health: "unknown" })])];
    expect(seatHealth(seat("websites"), withUnknown)).toBe("unknown");
    const failing = [mod("agency", [check("a", { health: "failing" }), check("b", { health: "unknown" })])];
    expect(seatHealth(seat("websites"), failing)).toBe("failing");
  });
});

describe("doneSoFar", () => {
  it("keeps only finished actions of the seat, newest first, at most 8", () => {
    const rows = [
      action("agency.build_site", "execute.finish", "2026-10-27T08:00:00Z"),
      action("agency.deploy_site", "execute.refused", "2026-10-27T08:30:00Z"),
      action("agency.mockup", "preview", "2026-10-27T08:40:00Z"),
      action("agency.prospect", "proposal.created", "2026-10-27T08:50:00Z"),
      action("agency.draft_outreach", "execute.finish", "2026-10-27T09:00:00Z"),
      action("mail.draft_reply", "execute.finish", "2026-10-27T09:10:00Z"),
    ];
    const out = doneSoFar(seat("websites"), rows);
    expect(out.map((r) => r.subject)).toEqual(["agency.draft_outreach", "agency.build_site"]);
    expect(out[0]?.at).toBe("2026-10-27T09:00:00Z");
    const many = Array.from({ length: 12 }, (_, i) =>
      action("mail.apply_labels", "execute.finish", `2026-10-27T0${i % 9}:00:00Z`));
    expect(doneSoFar(seat("personal"), many)).toHaveLength(8);
  });

  it("the Content seat counts academy, youtube, community and reddit finishes", () => {
    const rows = [action("academy.publish_tutorial", "execute.finish", READ),
                  action("youtube.upload", "execute.finish", READ),
                  action("community.schedule_youtube_shorts", "execute.finish", READ),
                  action("reddit.draft_post", "execute.finish", READ),
                  action("agency.build_site", "execute.finish", READ)];
    expect(doneSoFar(seat("academy"), rows)).toHaveLength(4);
  });

  it("the last action is the newest finish, or null when there is none", () => {
    const rows = [action("qa.check_site", "execute.finish", "2026-10-27T08:00:00Z"),
                  action("qa.verify_runs", "execute.finish", "2026-10-27T09:00:00Z")];
    expect(seatLastAction(seat("qa"), rows)).toMatchObject(
      { subject: "qa.verify_runs", at: "2026-10-27T09:00:00Z" });
    expect(seatLastAction(seat("seo"), rows)).toBeNull();
  });
});

describe("seatRoster", () => {
  it("sums runs and takes the latest run across the seat's agents", () => {
    const r = seatRoster({ ...seat("personal"), rosterAgents: ["inbox", "sites"] }, [
      agent("inbox", { runs_24h: 3, last_run_at: "2026-10-27T08:00:00Z" }),
      agent("sites", { runs_24h: 2, last_run_at: "2026-10-27T09:00:00Z" }),
    ]);
    expect(r).toEqual({ runs24h: 5, lastRunAt: "2026-10-27T09:00:00Z", ready: true });
  });

  it("is null for a seat with no agent and unknown when an agent is missing or unread", () => {
    expect(seatRoster({ ...seat("academy"), rosterAgents: [] }, [])).toBeNull();
    expect(seatRoster(seat("seo"), [agent("qa")])).toBe("unknown");
    expect(seatRoster(seat("personal"), [agent("sites")])).toBe("unknown");
    expect(seatRoster(seat("websites"), null)).toBe("unknown");
  });
});

describe("seatRemediation", () => {
  const t = (owner: string, open = true) => ({ owner, open }) as RemediationTask;
  it("counts open tasks owned by the seat's agents or its key; null when unread", () => {
    const tasks = [t("qa"), t("qa"), t("seo"), t("seo", false), t("content")];
    expect(seatRemediation(seat("qa"), tasks)).toBe(2);
    expect(seatRemediation(seat("seo"), tasks)).toBe(1);
    expect(seatRemediation(seat("academy"), tasks)).toBe(1);
    expect(seatRemediation(seat("bizops"), tasks)).toBe(0);
    expect(seatRemediation(seat("qa"), null)).toBeNull();
  });
});

describe("AgencyBody", () => {
  const read = <T,>(data: T | null, error: string | null = null) =>
    ({ data, error, fetchedAt: data || error ? new Date(READ) : null });
  const props = (modules: ModuleTile[] | null) => ({
    overview: read(modules ? { as_of: READ, modules, pending_approvals: 0, watch_only: true } : null,
                   modules ? null : "HTTP 502"),
    roster: read({ as_of: READ, agents: [agent("sites", { runs_24h: 4 }), agent("inbox"),
                                          agent("qa"), agent("seo"), agent("content")] }),
    leads: read({ as_of: READ, leads: [], total: 7, limit: 1, offset: 0,
                  counts: { new: 5, contacted: 2 }, why_empty: null }),
    approvals: read({ as_of: READ, watch_only: true, proposals: [
      { id: 11, action: "academy.publish_tutorial", tier: 2, expires_at: "2026-10-27T20:00:00Z" },
      { id: 12, action: "clips.post_reel", tier: 2, expires_at: "2026-10-27T20:00:00Z" },
    ] } as never),
    actions: read({ as_of: READ, activity: [
      action("agency.build_site", "execute.finish", "2026-10-27T08:00:00Z"),
      action("guardian.restart", "execute.finish", "2026-10-27T08:10:00Z"),
    ] } as never),
    remediation: read({ served_at: READ, open: 2, tasks: [
      { owner: "qa", open: true }, { owner: "qa", open: true }, { owner: "seo", open: false },
    ] } as never),
    now: new Date("2026-10-27T10:00:00Z"),
  });
  const html = (modules: ModuleTile[] | null, over: Record<string, unknown> = {}) =>
    renderToStaticMarkup(createElement(AgencyBody, { ...props(modules), ...over }));

  it("renders eight seats under the header, each with a read time", () => {
    const h = html([mod("agency", [check("leads_waiting")])]);
    expect(h).toContain("Agency · 8 seats");
    expect([...h.matchAll(/data-seat="([a-z]+)"/g)].map((m) => m[1])).toEqual(KEYS);
    expect(h.match(/id="seat-/g)).toHaveLength(8);
    expect((h.match(/read \d\d:\d\d/g) ?? []).length).toBeGreaterThanOrEqual(8);
  });

  it("an unread overview makes every seat Unknown", () => {
    const h = html(null);
    expect(h.match(/data-seat="[a-z]+" data-health="unknown"/g)).toHaveLength(8);
    expect(h).toContain("Unknown");
  });

  it("a seat whose module is not registered says Unknown in the grey word", () => {
    const h = html([mod("agency", [check("leads_waiting")])]);
    const seo = h.slice(h.indexOf('id="seat-seo"'), h.indexOf('id="seat-research"'));
    expect(seo).toContain('data-health="unknown"');
    expect(seo).toContain('class="cmd-word-unknown">Unknown<');
  });

  it("a known seat word carries its health, so Failing renders red, not like OK", () => {
    const h = html([mod("agency", [check("leads_waiting")])]);
    const words = [...h.matchAll(/class="cmd-seat-w" data-health="([a-z]+)">([A-Za-z]+)</g)];
    expect(words.length).toBeGreaterThan(0);
    for (const [, health, word] of words) {
      expect((word ?? "").toLowerCase()).toBe(health);
    }
  });

  it("each card shows its role, last action with its time, and open remediation", () => {
    const h = html([mod("agency", [check("leads_waiting")])]);
    const web = h.slice(h.indexOf('id="seat-websites"'), h.indexOf('id="seat-personal"'));
    expect(web).toContain(seat("websites").role);
    expect(web).toMatch(/Last action[^<]*<\/[a-z]+>[^]*agency\.build_site[^]*08:00/);
    const qa = h.slice(h.indexOf('id="seat-qa"'));
    expect(qa).toMatch(/Open remediation[^]*>2</);
    expect(qa).toContain("No action finished");
  });

  it("an unread remediation board is Unknown on every card, never 0", () => {
    const h = html([mod("agency", [check("leads_waiting")])], {
      remediation: { data: null, error: "HTTP 503", fetchedAt: new Date(READ) } });
    expect(h.match(/Open remediation/g)).toHaveLength(8);
    expect(h).toContain("remediation could not be read");
    expect(h).not.toMatch(/Open remediation[^]{0,80}>0</);
  });

  it("shows no retired name, even from live data, and counts what it hid", () => {
    const h = html([mod("agency", [check("leads_waiting")])]);
    expect(h).not.toMatch(/guardian|flycheap|instagram|tiktok|whop|postiz|amsm|concierge|sentinel/i);
    expect(h).not.toMatch(/\bclips\b/i);
    expect(h).not.toContain("clips_");
    expect(h).not.toMatch(/reels?\b/i);
    expect(h).toMatch(/from retired lanes not shown/);
  });

  it("seat checks never print a retired key as a label", () => {
    const yt = mod("youtube", [check("clips_stats"), check("clips_token"), check("amsm_stats")]);
    const labels = seatChecks(seat("academy"), [yt]).map((c) => c.label);
    expect(labels).toEqual(["Channel stats", "Token", "Community channel stats"]);
    const odd = mod("agency", [check("tiktok_followers")]);
    const out = seatChecks(seat("websites"), [odd]);
    expect(out.map((c) => c.label).join(" ")).not.toMatch(/tiktok/i);
  });

  it("states the tutorial hour as code default, and that decisions are answered in Slack", () => {
    const h = html(null);
    expect(h).toContain("17:00 UTC");
    expect(h).toContain("not read from the server");
    expect(h).toContain("#agency-decisions");
    expect(h).toContain("Atlas Academy");
  });
});
