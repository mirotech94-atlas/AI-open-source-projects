/**
 * Command Center v2 look: the health bar, the stage bar and the hero stats
 * follow the same rule as everything else: unread is UNKNOWN, never 0 or OK.
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
const agent = (name: string): AgentSummary => ({
  name, title: name, role: name, status: "ok", shift_state: "done", last_shift_at: AT,
  last_shift_outcome: "ok", attempts_today: 1, today_output: 2, alert_unrouted: false,
  read_at: AT, next_shift_at: null,
} as unknown as AgentSummary);
const tile = (module: string, health: string): ModuleTile => ({
  module, title: module, health, last_check_at: AT, next_check_at: null, enabled: true,
  latest_finding: null, checks: [{ name: `${module}_c`, health, detail: "d", observed_at: AT,
    next_run_at: null, interval_s: 3600 }],
} as unknown as ModuleTile);
const pipe = (leads: string): PipelineRead => ({
  served_at: AT, read_at: AT, sites: { state: "ok" }, leads: { state: leads, reason: "no key" },
  rows: ["new", "new", "won", ""].map((stage, i) => ({ kind: "lead", client: `c${i}`, stage,
    next_step: "x", health: "ok", reason: "", read_at: AT })),
} as unknown as PipelineRead);
const MONEY = { served_at: AT, read_at: AT, revenue: { value: 1, state: "ok", reason: "", read_at: AT },
  costs: { free_quota: { pools: {}, read_at: AT }, perplexity: { used: 1, cap: 9, read_at: AT },
    places: { used: 1, cap: 9, read_at: AT } } } as unknown as MoneyRead;

function render(over: Partial<FloorProps> = {}): string {
  const props: FloorProps = {
    agents: read({ agents: [agent("scout")] } as never),
    pipe: read(pipe("ok")), money: read(MONEY),
    overview: read({ modules: [tile("a", "ok"), tile("b", "ok"), tile("c", "degraded"),
      tile("d", "unknown")], pending_approvals: 0, watch_only: false } as unknown as Overview),
    pending: { count: 0, readAt: AT },
    needsYou: createElement("section", { id: "decisions" }, "NEEDS-YOU"),
    leads: read({ leads: [], total: 0 } as never),
    ...over,
  };
  return renderToStaticMarkup(createElement(MemoryRouter, null,
    createElement(CommandFloorBody, props)));
}

describe("Command Center v2 bars", () => {
  it("a failed overview makes the health bar UNKNOWN with the word", () => {
    const h = render({ overview: read<Overview>(null, "timeout") });
    const bar = h.slice(h.indexOf('class="cmd-healthbar"'));
    expect(bar).toContain('data-health="unknown"');
    expect(bar.slice(0, 700)).toContain("Unknown");
    expect(bar.slice(0, 700)).not.toMatch(/data-health="ok"/);
  });

  it("an overview still loading says reading, never read failed", () => {
    const h = render({ overview: read<Overview>(null, null) });
    const bar = h.slice(h.indexOf('class="cmd-healthbar"'), h.indexOf('class="cmd-healthbar"') + 1200);
    expect(bar).toContain('data-health="unknown"');
    expect(bar).toContain("reading…");
    expect(bar).not.toContain("read failed");
  });

  it("a read overview shows counted segments, none carrying data-key", () => {
    const h = render();
    const bar = h.slice(h.indexOf('class="cmd-healthbar"'), h.indexOf('class="cmd-healthbar"') + 1200);
    expect(bar).toContain('data-health="ok"');
    expect(bar).toContain("OK: 2");
    expect(bar).not.toContain("data-key");
    expect(h.match(/data-key="/g)).toHaveLength(5);
  });

  it("an unknown leads read makes the stage bar UNKNOWN, not 0", () => {
    const h = render({ pipe: read(pipe("unknown")) });
    const at = h.indexOf('class="cmd-stagebar"');
    expect(at).toBeGreaterThan(-1);
    const bar = h.slice(at, at + 500);
    expect(bar).toContain('data-health="unknown"');
    expect(bar).toContain("Unknown");
    expect(bar).not.toMatch(/>0</);
  });

  it("a read pipeline shows stage counts and a real 'no stage' count", () => {
    const h = render();
    const at = h.indexOf('class="cmd-stagebar"');
    const bar = h.slice(at, at + 1600);
    expect(bar).toContain("new");
    expect(bar).toContain("no stage");
    expect(bar).not.toContain("data-key");
  });

  it("the hero carries the state word and stats from the checks", () => {
    const h = render();
    const hero = h.slice(h.indexOf('class="br-hero"'), h.indexOf("</figure>"));
    expect(hero).toContain("Degraded");
    expect(hero).toContain("read ");
  });

  it("an unread hero says Unknown and shows no figures", () => {
    const h = render({ overview: read<Overview>(null, "timeout") });
    const hero = h.slice(h.indexOf('class="br-hero"'), h.indexOf("</figure>"));
    expect(hero).toContain("cmd-word-unknown");
    expect(hero).toContain("checks not read");
    expect(hero).not.toContain("br-stats");
  });
});
