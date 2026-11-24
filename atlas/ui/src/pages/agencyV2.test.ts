/**
 * Agency tab, design v2: glance panel, radial map, agent cards. Static markup
 * only. A failed read is UNKNOWN everywhere (grey, dashed, the word), never a
 * zero and never a stale OK; the agent count comes from the read.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AgencyActivity, AgentActivity, AgentSummary, Approvals } from "../api";
import { AgentActivityBody } from "./AgentActivity";
import { AgencyGlancePanel, AgencyMapBody } from "./AgencyTab";
import { radialLayout } from "./agentMapLayout";
import type { Read } from "./homeModel";
import { ROSTER_AGENTS } from "./roster";

const NOW = Date.parse("2026-11-23T12:00:00Z");
const AT = new Date(NOW);
const ISO = "2026-11-23T11:59:00Z";

function agent(name: string, over: Partial<AgentSummary> = {}): AgentSummary {
  return {
    name, title: name, display: name, brief: `${name} brief`, need: null, available: true,
    providers: [], tools: 1, last_run_at: null, last_outcome: null, runs_24h: 0,
    model: null, model_at: null, handoff: [], memory_scope: [], ready: true, reasons: [],
    group: name === "inbox" ? "lead" : "functions", next_shift_at: null, status: "ok",
    role: `${name} role`, ...over,
  };
}
const ok = <T,>(data: T): Read<T> => ({ data, error: null, fetchedAt: AT });
const failed = <T,>(error = "502"): Read<T> => ({ data: null, error, fetchedAt: AT });
const nine = [...ROSTER_AGENTS].map((n) => agent(n));
const approvals = ok({ proposals: [], watch_only: true, as_of: ISO } as unknown as Approvals);

function act(name: string): AgentActivity {
  return {
    name, label: name, title: name, modules: [name],
    now: { read_at: ISO, headline: "idle headline", in_flight: 0, waiting: 0, cards: [],
           running_jobs: [], last_run: null, last_run_known: true, unread: [] },
    yesterday: { read_at: ISO, day: "2026-11-22", from: ISO, to: ISO, runs: 0, failed_runs: 0,
                 last_run_at: null, actions_ok: 0, actions_failed: 0, last_actions: [],
                 cards_expired: 0, unread: [] },
    issues: { read_at: ISO, count: 0, items: [], unread: [] },
  };
}
const activity = (names: string[]): Read<AgencyActivity> =>
  ok({ served_at: ISO, read_at: ISO, agents: names.map(act), errors: {} });

const map = (roster: Read<{ agents: AgentSummary[] }>, extra: object = {}): string =>
  renderToStaticMarkup(createElement(AgencyMapBody, {
    roster, approvals, now: NOW, radial: true, selected: null, onSelect: () => {}, ...extra,
  }));
const count = (html: string, re: RegExp): number => (html.match(re) ?? []).length;

describe("radialLayout", () => {
  it("places every agent once, inside the frame, lead on top", () => {
    const l = radialLayout(nine);
    expect(l.nodes).toHaveLength(9);
    expect(new Set(l.nodes.map((n) => `${n.x},${n.y}`)).size).toBe(9);
    for (const n of l.nodes) {
      expect(n.x).toBeGreaterThan(0); expect(n.x).toBeLessThan(l.frame.w);
      expect(n.y).toBeGreaterThan(0); expect(n.y).toBeLessThan(l.frame.h);
    }
    expect(l.nodes[0]?.name).toBe("inbox");
    expect(l.nodes[0]?.y).toBeLessThan(l.frame.cy);
    expect(l.links.filter((k) => k.kind === "spoke")).toHaveLength(9);
  });
  it("draws a handoff curve only between two roster agents", () => {
    const l = radialLayout([agent("inbox", { handoff: ["sites", "ghost"] }), agent("sites")]);
    const h = l.links.filter((k) => k.kind === "handoff");
    expect(h).toHaveLength(1);
    expect(h[0]?.d).toContain("Q");
  });
  it("an empty roster has no node", () => {
    expect(radialLayout([]).nodes).toEqual([]);
  });
});

describe("Agency radial map", () => {
  it("a 9-agent roster draws 9 nodes and says 9 agents from the read", () => {
    const html = map(ok({ agents: nine }));
    expect(count(html, /data-agent="/g)).toBe(9);
    expect(html).toContain("9 agents");
    expect(html).toContain('data-agent="mmt"');
  });
  it("the count follows the read, not a constant", () => {
    const html = map(ok({ agents: nine.slice(0, 3) }));
    expect(count(html, /data-agent="/g)).toBe(3);
    expect(html).toContain("3 agents");
  });
  it("never draws a retired name the server still sent", () => {
    const html = map(ok({ agents: [...nine, agent("concierge"), agent("scheduler")] }));
    expect(count(html, /data-agent="/g)).toBe(9);
    expect(html).not.toContain('data-agent="concierge"');
    expect(html).not.toContain('data-agent="scheduler"');
  });
  it("a roster that cannot be read at all says Unknown and draws no node", () => {
    const html = map(failed(), { fresh: true });
    expect(html).toContain("data-read-failed");
    expect(html).toContain("cmd-word-unknown");
    expect(html).toContain("Unknown: the roster could not be read.");
    expect(html).not.toContain("data-flow");
  });
  it("a failed re-read: every node UNKNOWN, word in label, no flow", () => {
    const html = map({ ...ok({ agents: nine }), error: "502" }, { fresh: true });
    expect(count(html, /data-state="unknown"/g)).toBe(9);
    expect(count(html, /aria-label="[^"]*Unknown/g)).toBe(9);
    expect(html).not.toContain("data-flow");
    expect(html).not.toContain("data-fresh");
    expect(html).not.toContain('data-state="ready"');
    expect(html).not.toContain("9 agents");
  });
  it("flow only with a fresh read", () => {
    expect(map(ok({ agents: nine }), { fresh: true })).toContain("data-flow");
    expect(map(ok({ agents: nine }), { fresh: false })).not.toContain("data-flow");
  });
  it("an agent whose own status is unknown is UNKNOWN, not ready", () => {
    const html = map(ok({ agents: [agent("inbox", { status: "unknown" })] }));
    expect(html).toContain('data-state="unknown"');
    expect(html).not.toContain('data-state="ready"');
  });
});

describe("Agency glance", () => {
  const render = (a: Read<{ as_of: string; agents: AgentSummary[] }>,
                  p: Read<Approvals>, o: Read<never>): string =>
    renderToStaticMarkup(createElement(AgencyGlancePanel, { agents: a, approvals: p, overview: o }));
  it("four cells, each with a read time", () => {
    const html = render(ok({ as_of: ISO, agents: nine }), approvals,
      ok({ as_of: ISO, modules: [], pending_approvals: 0, watch_only: true }) as never);
    expect(count(html, /data-cell="/g)).toBe(4);
    expect(count(html, /read \d\d:\d\d/g)).toBe(4);
    expect(html).toContain(">9<");
  });
  it("unread cells are UNKNOWN with the word, never 0", () => {
    const html = render(failed(), failed(), failed() as never);
    expect(count(html, /data-health="unknown"/g)).toBe(4);
    expect(count(html, /cmd-word-unknown/g)).toBe(4);
    expect(html).toContain("read failed");
    expect(html).not.toMatch(/>0</);
  });
});

describe("Agency agent cards", () => {
  const render = (r: Read<{ agents: AgentSummary[] }> | undefined, sel: string | null = null,
                  names: string[] = [...ROSTER_AGENTS]): string =>
    renderToStaticMarkup(createElement(AgentActivityBody, {
      activity: activity(names), roster: r, selected: sel, onSelect: () => {},
    }));
  it("roster read failed: every card UNKNOWN", () => {
    const html = render(failed());
    expect(count(html, /<article[^>]*data-health="unknown"/g)).toBe(9);
    expect(html).toContain("read failed");
  });
  it("shows role, brief, current work, badge, read time and a toggle", () => {
    const html = render(ok({ agents: [agent("inbox", { last_outcome_detail: "sorted 4 mails" })] }),
                        null, ["inbox"]);
    expect(html).toContain("inbox role");
    expect(html).toContain("inbox brief");
    expect(html).toContain("Current work");
    expect(html).toContain("sorted 4 mails");
    expect(html).toContain('class="badge"');
    expect(html).toContain('aria-expanded="false"');
  });
  it("current work falls back to the activity headline", () => {
    expect(render(ok({ agents: [agent("inbox")] }), null, ["inbox"])).toContain("idle headline");
  });
  it("the selected agent's card is expanded", () => {
    const html = render(ok({ agents: nine }), "sites");
    expect(count(html, /aria-expanded="true"/g)).toBe(1);
    expect(count(html, /aria-expanded="false"/g)).toBe(8);
  });
  it("a status of idle is not shown as OK", () => {
    const html = render(ok({ agents: [agent("qa", { status: "idle" })] }), null, ["qa"]);
    expect(html).toContain("Idle");
    expect(html).not.toContain('data-health="ok"');
  });
});
