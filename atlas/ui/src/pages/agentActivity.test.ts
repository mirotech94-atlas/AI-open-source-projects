import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AgencyActivity, AgentActivity } from "../api";
import { AgentActivityBody } from "./AgentActivity";
import {
  agentHealth, agentMeter, issueLines, issueText, nowLines, yesterdayLines,
} from "./agentActivityModel";
import type { Read } from "./homeModel";
import { ROSTER_AGENTS } from "./roster";

const AT = "2026-10-28T16:00:00+00:00";

function agent(name: string, over: Partial<AgentActivity> = {}): AgentActivity {
  return {
    name, label: name, title: name, modules: [name],
    now: { read_at: AT, headline: "idle", in_flight: 0, waiting: 0, cards: [],
           running_jobs: [], last_run: null, last_run_known: true, unread: [] },
    yesterday: { read_at: AT, day: "2026-10-27", from: AT, to: AT, runs: 0, failed_runs: 0,
                 last_run_at: null, actions_ok: 0, actions_failed: 0, last_actions: [],
                 cards_expired: 0, unread: [] },
    issues: { read_at: AT, count: 0, items: [], unread: [] },
    ...over,
  };
}

const ok = <T,>(data: T): Read<T> => ({ data, error: null, fetchedAt: new Date(AT) });
const body = (agents: AgentActivity[]): AgencyActivity =>
  ({ served_at: AT, read_at: AT, agents, errors: {} });
const render = (r: Read<AgencyActivity>): string =>
  renderToStaticMarkup(createElement(AgentActivityBody, { activity: r }));

describe("agent activity panel", () => {
  it("draws one card per roster agent, each section with its read time", () => {
    const html = render(ok(body([...ROSTER_AGENTS].map((n) => agent(n)))));
    expect((html.match(/data-agent="/g) ?? [])).toHaveLength(9);
    expect((html.match(/read \d\d:\d\d/g) ?? []).length).toBeGreaterThanOrEqual(24);
    expect(html).toContain("2026-10-27");
  });

  it("never draws a retired agent", () => {
    const html = render(ok(body([agent("inbox"), agent("concierge")])));
    expect(html).not.toContain('data-agent="concierge"');
  });

  it("a failed read is Unknown, grey and dashed, never an empty panel", () => {
    const html = render({ data: null, error: "HTTP 503", fetchedAt: new Date(AT) });
    expect(html).toContain("cmd-word-unknown");
    expect(html).toContain('data-health="unknown"');
    expect(html).toContain("read failed");
  });

  it("an unread section is Unknown, never zero", () => {
    const a = agent("qa", {
      now: { ...agent("qa").now, in_flight: null, waiting: null, last_run_known: false,
             unread: ["work board"] },
      yesterday: { ...agent("qa").yesterday, runs: null, failed_runs: null,
                   actions_ok: null, actions_failed: null, cards_expired: null },
      issues: { read_at: AT, count: 0, items: [], unread: ["checks"] },
    });
    for (const l of [...nowLines(a), ...yesterdayLines(a)]) {
      expect(l.text).not.toMatch(/\b0\b/);
    }
    expect(nowLines(a).some((l) => l.health === "unknown")).toBe(true);
    expect(yesterdayLines(a).filter((l) => l.health === "unknown")).toHaveLength(2);
    expect(issueLines(a)).toEqual([{ text: "checks not read", health: "unknown" }]);
    expect(agentHealth(a)).toBe("unknown");
    const html = render(ok(body([a])));
    expect(html).toContain('data-health="unknown"');
  });

  it("names cards, failures and issues from the read", () => {
    const a = agent("sites", {
      now: { ...agent("sites").now, waiting: 1,
             cards: [{ id: 3, action: "agency.send_preview", tier: 2, expires_at: AT }],
             last_run: { at: AT, failed: true } },
      yesterday: { ...agent("sites").yesterday, runs: 4, failed_runs: 1, actions_ok: 2,
                   actions_failed: 1, cards_expired: 2 },
      issues: { read_at: AT, count: 2, unread: [], items: [
        { kind: "job_stalled", job: "agency_sweep", at: AT },
        { kind: "check_unknown", module: "agency", check: "leads", stale: true, at: AT },
      ] },
    });
    expect(nowLines(a)[1]?.text).toBe("1 card waiting: agency.send_preview");
    expect(yesterdayLines(a)[0]?.text).toBe("4 runs, 1 failed");
    expect(yesterdayLines(a)[1]?.text).toBe("3 actions ran, 1 failed");
    expect(yesterdayLines(a).at(-1)?.text).toBe("2 cards expired unanswered");
    expect(issueLines(a).map((l) => l.health)).toEqual(["degraded", "unknown"]);
    expect(issueText({ kind: "check_unknown", module: "agency", check: "leads",
                       stale: true, at: null })).toBe("agency.leads stale");
    expect(agentHealth(a)).toBe("failing");
  });

  it("a quiet, fully read agent is OK and says so in words", () => {
    const a = agent("seo");
    expect(issueLines(a)).toEqual([{ text: "no issue found", health: "plain" }]);
    expect(nowLines(a).map((l) => l.text)).toEqual(
      ["nothing in flight", "no card waiting", "has never run"]);
    expect(agentHealth(a)).toBe("ok");
  });

  it("the card's progress bar is yesterday's real action split, never a guess", () => {
    const y = agent("x").yesterday;
    const m = agentMeter(agent("x", { yesterday: { ...y, actions_ok: 3, actions_failed: 1 } }));
    expect(m).toEqual({ state: "read", ok: 3, failed: 1, total: 4, okPct: 75, failedPct: 25 });
    // A real empty day is a real zero: an empty bar, not UNKNOWN.
    expect(agentMeter(agent("x"))).toEqual(
      { state: "read", ok: 0, failed: 0, total: 0, okPct: 0, failedPct: 0 });
    // An unread figure is UNKNOWN, never 0 and never full.
    expect(agentMeter(agent("x", { yesterday: { ...y, actions_ok: null } })))
      .toEqual({ state: "unknown" });
    expect(agentMeter(agent("x", { yesterday: { ...y, actions_failed: null } })))
      .toEqual({ state: "unknown" });
    const html = render(ok(body([agent("sites", {
      yesterday: { ...y, actions_ok: 3, actions_failed: 1 } }), agent("qa", {
      yesterday: { ...y, actions_ok: null } })])));
    expect(html).toContain('data-meter="read"');
    expect(html).toContain("75%");
    expect(html).toContain('data-meter="unknown"');
  });
});
