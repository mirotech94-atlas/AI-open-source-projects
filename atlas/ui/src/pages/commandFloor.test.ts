/**
 * The short Command Center (the owner 2026-10-29: "don't make the Command Center
 * so long; keep the page short with only the most important sections").
 * One screen: the strip, Roster, Needs you and one compact Health+Money
 * panel. Everything else lives on the Agency tab. Static render, pure reads.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { AgentSummary, ModuleTile, MoneyRead, Overview, PipelineRead } from "../api";
import { CommandFloorBody, type FloorProps } from "./CommandSections";

const AT = "2026-10-29T09:00:00Z";
const read = <T,>(data: T | null, error: string | null = null) =>
  ({ data, error, fetchedAt: data !== null || error ? new Date(AT) : null });

const NAMES = ["scout", "sales", "sites", "seo", "research", "qa", "ops", "writer"];
const agent = (name: string, over: Partial<AgentSummary> = {}): AgentSummary => ({
  name, title: name, role: name, status: "ok", shift_state: "done", last_shift_at: AT,
  last_shift_outcome: "ok", attempts_today: 1, today_output: 2, alert_unrouted: false,
  read_at: AT, next_shift_at: null, ...over,
} as unknown as AgentSummary);

const check = (name: string, health: string) => ({
  name, health, detail: `${name} detail`, observed_at: AT, next_run_at: null, interval_s: 3600,
});
const tile = (module: string, health: string): ModuleTile => ({
  module, title: module, health, checks: [check(`${module}_check`, health)], last_check_at: AT,
  next_check_at: null, enabled: true, latest_finding: null,
} as unknown as ModuleTile);

const PIPE = { served_at: AT, read_at: AT, sites: { state: "ok" }, leads: { state: "ok" },
  rows: [1, 2, 3].map((i) => ({ kind: "lead", client: `c${i}`, stage: "new", next_step: "x",
                                 health: "ok", reason: "", read_at: AT })),
} as unknown as PipelineRead;

const MONEY = { served_at: AT, read_at: AT, revenue: { value: 120, state: "ok", reason: "",
  read_at: AT },
  costs: { free_quota: { pools: { groq: { requests_left: 5, requests_limit: 10,
    tokens_limit: null, tokens_used_24h: 9, blocked_s: 0, why: null } }, read_at: AT },
    perplexity: { used: 3, cap: 100, read_at: AT }, places: { used: 7, cap: 500, read_at: AT } },
} as unknown as MoneyRead;

function render(over: Partial<FloorProps> = {}): string {
  const props: FloorProps = {
    agents: read({ agents: NAMES.map((n) => agent(n)) } as never),
    pipe: read(PIPE),
    money: read(MONEY),
    overview: read({ modules: [tile("seo", "ok"), tile("shop", "failing"),
                               tile("mail", "degraded"), tile("qa", "unknown")],
                     pending_approvals: 0, watch_only: false } as unknown as Overview),
    pending: { count: 2, readAt: AT },
    needsYou: createElement("section", { id: "decisions" }, "NEEDS-YOU"),
    nextUp: null,
    ...over,
  };
  return renderToStaticMarkup(createElement(MemoryRouter, null,
    createElement(CommandFloorBody, props)));
}

describe("the short Command Center", () => {
  it("keeps only the strip, Roster, Needs you and Health+Money", () => {
    const h = render();
    expect(h).toContain('class="cmd-strip"');
    expect(h).toContain('id="cmd-roster-h"');
    expect(h).toContain("NEEDS-YOU");
    expect(h).toContain('id="cmd-vitals-h"');
    for (const gone of ['id="cmd-pipeline-h"', 'id="cmd-costs-h"', 'id="cmd-alerts-h"',
                        'id="cmd-s-search-h"', 'id="cmd-s-today-h"', 'id="cmd-s-money-h"',
                        'id="cmd-s-health-h"', 'id="cmd-schedule-h"', 'id="agency-desk"',
                        "Free-tier pools"]) {
      expect(h, gone).not.toContain(gone);
    }
  });

  it("the pipeline is a count in the strip that opens the Agency tab", () => {
    const h = render();
    expect(h).toMatch(/<a[^>]*href="\/agency#pipeline"[^>]*>3<\/a>/);
  });

  it("the roster is one row per agent", () => {
    const h = render();
    const body = h.slice(h.indexOf("<tbody>"), h.indexOf("</tbody>"));
    expect(body.match(/<tr /g)).toHaveLength(8);
  });

  it("lists only FAILING and DEGRADED; UNKNOWN is a grey dashed chip with the word", () => {
    const h = render();
    const panel = h.slice(h.indexOf('id="cmd-vitals-h"'));
    expect(panel).toContain('data-health="failing"');
    expect(panel).toContain('data-health="degraded"');
    expect(panel).not.toMatch(/<li[^>]*data-health="unknown"[^>]*>[^]*qa_check/);
    expect(panel).toMatch(/class="cs-chip-unknown"[^>]*>[^<]*1[^<]*Unknown/);
    expect(panel).toContain("read ");
  });

  it("an unread overview says Unknown, never zero or OK", () => {
    const h = render({ overview: read<Overview>(null, "timeout") });
    const panel = h.slice(h.indexOf('id="cmd-vitals-h"'));
    expect(panel).toContain("cmd-word-unknown");
    expect(panel).not.toContain("All checks OK");
  });

  it("money keeps its figures and read times", () => {
    const h = render();
    const panel = h.slice(h.indexOf('id="cmd-vitals-h"'));
    expect(panel).toContain("$120");
    expect(panel).toContain("Places");
  });

  it("an undelivered FAILING alert stays visible as UNKNOWN", () => {
    const h = render({ agents: read({ agents: NAMES.map((n) => agent(n, {
      alert_unrouted: n === "qa" })) } as never) });
    const panel = h.slice(h.indexOf('id="cmd-vitals-h"'));
    expect(panel).toContain("alert channel");
  });

  it("sits every section left or right of the ring, ring first in the DOM", () => {
    const h = render({ leads: read({ leads: [], total: 0 } as never) });
    const ring = h.indexOf('class="cmd-ring-col"');
    const left = h.indexOf('data-side="left"');
    const right = h.indexOf('class="cmd-flank" data-side="right"');
    expect(ring).toBeGreaterThan(-1);
    expect(left).toBeGreaterThan(ring);
    expect(right).toBeGreaterThan(left);
    expect(h).toContain('aria-label="ATLAS status ring"');
    expect(h).toContain('aria-label="Command Center, left of the ATLAS ring"');
    expect(h).toContain('aria-label="Command Center, right of the ATLAS ring"');
    // Nothing is left out: all five tiles, Roster, Needs you, Health & money, Leads, both charts.
    for (const want of ['data-key="ok', 'id="cmd-roster-h"', "NEEDS-YOU", 'id="cmd-vitals-h"',
                        'id="cmd-leads-grid-h"', 'id="cmd-chart-pipeline-h"',
                        'id="cmd-chart-shifts-h"']) {
      expect(h, want).toContain(want);
    }
    expect(h.match(/data-key="/g)).toHaveLength(5);
    // Nothing sits outside a flank except the ring.
    const after = h.slice(h.indexOf("</figure>"));
    expect(after.indexOf('class="cmd-flank"')).toBeGreaterThan(-1);
  });

  it("both flanks carry KPI tiles", () => {
    const h = render();
    const r = h.indexOf('class="cmd-flank" data-side="right"');
    expect(h.slice(0, r)).toMatch(/data-side="left"[^>]*>[^]*data-key=/);
    expect(h.slice(r)).toContain('data-key="');
  });
});
