/**
 * Renders `AgentMap` to static markup (no jsdom -- see `agentMapLayout.
 * test.ts` for the pure grouping/connection logic this only has to trust)
 * and asserts on what actually reaches the DOM: one card per agent, the
 * lead card above the two lanes, no `data-connected` anywhere until a card
 * is selected (static markup is always the initial, nothing-selected
 * render), and a not-ready or unknown card never sharing "ready"'s look.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AgentSummary } from "../api";
import { AgentMap, detailLinks } from "./AgentMap";

function agent(over: Partial<AgentSummary> = {}): AgentSummary {
  return {
    name: "ops", title: "Ops", display: "Ops", brief: "watches infra",
    need: null, available: true, providers: [], tools: 3,
    last_run_at: null, last_outcome: null, runs_24h: 0, model: null,
    model_at: null, handoff: [], memory_scope: [], ready: true,
    reasons: [], group: "functions", next_shift_at: null,
    ...over,
  };
}

function render(agents: AgentSummary[], readFailed = false): string {
  return renderToStaticMarkup(
    createElement(AgentMap, { agents, readFailed, onSelect: () => {} }));
}

describe("AgentMap renders the roster it is given", () => {
  it("one card per agent", () => {
    const markup = render([
      agent({ name: "concierge", group: "lead" }),
      agent({ name: "flycheap", group: "businesses" }),
      agent({ name: "ops", group: "functions" }),
    ]);
    expect((markup.match(/data-agent="/g) ?? [])).toHaveLength(3);
    expect(markup).toContain('data-agent="concierge"');
    expect(markup).toContain('data-agent="flycheap"');
    expect(markup).toContain('data-agent="ops"');
  });

  it("the lead card renders above the two lanes", () => {
    const markup = render([
      agent({ name: "concierge", group: "lead" }),
      agent({ name: "ops", group: "functions" }),
    ]);
    const leadAt = markup.indexOf('data-agent="concierge"');
    const laneAt = markup.indexOf("agent-map-lane");
    expect(leadAt).toBeGreaterThanOrEqual(0);
    expect(laneAt).toBeGreaterThan(leadAt);
  });

  it("puts a business in the Businesses lane and a role agent in Functions", () => {
    const markup = render([
      agent({ name: "flycheap", group: "businesses" }),
      agent({ name: "ops", group: "functions" }),
    ]);
    expect(markup).toContain("Businesses");
    expect(markup).toContain("Functions");
  });

  it("no card is connected until one is selected -- static markup is the initial state", () => {
    const markup = render([
      agent({ name: "concierge", group: "lead", handoff: ["ops"] }),
      agent({ name: "ops", group: "functions" }),
    ]);
    expect(markup).not.toContain("data-connected=");
    expect(markup).not.toContain("data-selected=");
  });

  it("no detail panel until a card is selected", () => {
    const markup = render([agent({ name: "ops" })]);
    expect(markup).not.toContain('data-testid="agent-map-detail"');
  });

  it("a ready, never-run agent gets the plain ready dot", () => {
    const markup = render([agent({ name: "ops", ready: true, last_run_at: null })]);
    expect(markup).toContain('data-state="ready"');
  });

  it("a ready agent that just ran gets the working dot", () => {
    const markup = render([
      agent({ name: "ops", ready: true, last_run_at: new Date().toISOString() }),
    ]);
    expect(markup).toContain('data-state="working"');
  });

  it("a not-ready agent gets its own dot, never ready's, and shows a reason", () => {
    const markup = render([
      agent({ name: "fiverr", ready: false,
              reasons: ["browser disabled — missing env: X"] }),
    ]);
    expect(markup).toContain('data-state="not-ready"');
    expect(markup).not.toContain('data-state="ready"');
    expect(markup).toContain("browser disabled");
  });

  it("a failed roster read draws every card unknown, never a stale look", () => {
    const markup = render([
      agent({ name: "ops", ready: true, last_run_at: new Date().toISOString() }),
      agent({ name: "fiverr", ready: false }),
    ], true);
    expect((markup.match(/data-state="unknown"/g) ?? [])).toHaveLength(2);
    expect(markup).not.toContain('data-state="ready"');
    expect(markup).not.toContain('data-state="working"');
    expect(markup).not.toContain('data-state="not-ready"');
  });

  it("each card is focusable and named for screen readers, with its brief", () => {
    const markup = render([agent({ name: "ops", brief: "watches infra" })]);
    expect(markup).toContain('tabindex="0"');
    expect(markup).toContain('role="button"');
    expect(markup).toContain("watches infra");
  });

  it("a card with a persona reads 'Name · Display'; one without keeps its display", () => {
    const markup = render([
      agent({ name: "concierge", display: "Concierge", group: "lead",
              persona: { agent: "concierge", name: "Ava", specialty: "chat", voice: "female" } }),
      agent({ name: "ops", display: "Ops" }),
    ]);
    expect(markup).toContain("Ava · Concierge");
    expect(markup).toMatch(/<span class="n">Ops<\/span>/);
  });

  it("an empty lane says so rather than rendering nothing", () => {
    const markup = render([agent({ name: "ops", group: "functions" })]);
    expect(markup).toContain("No agents in this lane.");
  });

  it("the details keep handoff direction: who it hands to, who hands to it", () => {
    const roster = [
      agent({ name: "ops", handoff: ["analyst", "ghost", "ops"], memory_scope: ["infra"] }),
      agent({ name: "analyst", memory_scope: ["infra"] }),
      agent({ name: "cloudhost", handoff: ["ops"], memory_scope: ["infra"] }),
      agent({ name: "inbox", memory_scope: ["infra"] }),
    ];
    expect(detailLinks("ops", roster)).toEqual({
      out: ["analyst"], in: ["cloudhost"], memory: ["analyst", "cloudhost", "inbox"],
    });
    expect(detailLinks("ghost", roster)).toEqual({ out: [], in: [], memory: [] });
  });
});
