/**
 * NeuralMap to static markup (no jsdom, like AgentMap.test.ts): one neuron
 * per agent, the core, synapses only for declared relationships, and
 * motion (the `nn-signals` paths) only where a real signal drives it.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AgentSummary } from "../api";
import { NeuralMap, type NeuralMapProps } from "./NeuralMap";

const NOW = new Date("2026-09-28T12:00:00Z").getTime();
const ago = (ms: number) => new Date(NOW - ms).toISOString();

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

function render(agents: AgentSummary[], over: Partial<NeuralMapProps> = {}): string {
  return renderToStaticMarkup(createElement(NeuralMap, { agents, now: NOW, ...over }));
}

const roster = [
  agent({ name: "concierge", display: "Concierge", group: "lead", handoff: ["ops"], memory_scope: ["people"],
          persona: { agent: "concierge", name: "Ava", specialty: "General conversation", voice: "female" } }),
  agent({ name: "flycheap", group: "businesses", memory_scope: ["people"] }),
  agent({ name: "ops", group: "functions" }),
];

const signals = (markup: string): string[] =>
  [...markup.matchAll(/<path[^>]*data-kind="(recent|speaking)"[^>]*>/g)].map((m) => m[0]);

describe("NeuralMap", () => {
  it("one focusable neuron per agent, and the ATLAS core", () => {
    const markup = render(roster);
    expect((markup.match(/data-agent="/g) ?? [])).toHaveLength(3);
    expect((markup.match(/role="button"/g) ?? [])).toHaveLength(3);
    expect(markup).toContain(">ATLAS</text>");
    expect(markup).toContain('aria-label="Ava · Concierge: watches infra.');
  });

  it("draws the handoff synapse, but shared memory only for a neuron in focus", () => {
    const markup = render(roster);
    expect(markup).toMatch(/data-kind="handoff" data-from="concierge" data-to="ops"/);
    expect(markup).not.toContain('data-kind="memory"');
    expect(markup).not.toContain("data-lit=");
  });

  it("selection lights that neuron's synapses, including shared memory", () => {
    const markup = render(roster, { selected: "concierge" });
    expect(markup).toMatch(/data-kind="handoff"[^>]*data-lit="true"/);
    expect(markup).toMatch(/data-kind="memory" data-from="concierge" data-to="flycheap" data-lit="true"/);
    expect(markup).toMatch(/data-agent="ops"[^>]*data-connected="handoff"/);
    expect(markup).toMatch(/data-agent="flycheap"[^>]*data-connected="memory"/);
  });

  it("nothing moves when no agent ran in the last ten minutes", () => {
    const markup = render([agent({ last_run_at: ago(11 * 60_000), runs_24h: 4 })]);
    expect(signals(markup)).toEqual([]);
    expect(markup).toContain("so nothing moves");
  });

  it("a signal runs only down the spoke of an agent that just ran", () => {
    const markup = render([
      agent({ name: "ops", last_run_at: ago(60_000) }),
      agent({ name: "analyst", last_run_at: ago(3_600_000) }),
    ]);
    const s = signals(markup);
    expect(s).toHaveLength(1);
    expect(s[0]).toContain('data-from="ops"');
    expect(s[0]).toContain('data-kind="recent"');
  });

  it("a failed roster read: every neuron unknown, no signal, no live colour", () => {
    const markup = render([agent({ last_run_at: ago(1000) })], { readFailed: true });
    expect(markup).toContain('data-state="unknown"');
    expect(markup).not.toMatch(/data-state="(ready|working)"/);
    expect(signals(markup)).toEqual([]);
    expect(markup).toContain("the roster could not be read");
  });

  it("the meeting's speaker lights its neuron and its spoke; only while speaking", () => {
    const idle = render(roster);
    expect(idle).not.toContain("data-speaking=");
    const markup = render(roster, { speaking: "ops" });
    expect(markup).toMatch(/data-agent="ops"[^>]*data-speaking="true"/);
    expect((markup.match(/nn-node[^>]*data-speaking="true"/g) ?? [])).toHaveLength(1);
    const s = signals(markup);
    expect(s).toHaveLength(1);
    expect(s[0]).toContain('data-kind="speaking"');
    expect(s[0]).toContain('data-from="ops"');
  });

  it("ATLAS speaking lights the core, not any neuron", () => {
    const markup = render(roster, { coreSpeaking: true });
    expect(markup).toMatch(/class="nn-core" data-speaking="true"/);
    expect(markup).not.toMatch(/nn-node[^>]*data-speaking/);
  });

  it("the compact frame names each neuron by first name only", () => {
    const markup = render(roster, { variant: "compact" });
    expect(markup).toContain('data-variant="compact"');
    expect(markup).toMatch(/<span class="n">Ava<\/span>/);
    expect(markup).not.toContain("<ellipse");
  });

  it("at phone width a not-ready agent's reason is still shown, and named to screen readers", () => {
    const blocked = agent({ name: "commerce", ready: false, reasons: ["module mirotech is switched off"],
      persona: { agent: "commerce", name: "Owen", specialty: "buyers", voice: "male" } });
    const markup = render([...roster, blocked], { variant: "compact" });
    expect(markup).toMatch(/class="nn-blocked"[\s\S]*Owen[\s\S]*module mirotech is switched off/);
    expect(markup).toMatch(/aria-label="[^"]*: module mirotech is switched off\."/);
    // A failed read lists no reasons: every neuron is unknown, not blocked.
    expect(render([blocked], { variant: "compact", readFailed: true })).not.toContain("nn-blocked");
  });

  it("an empty lane on a failed read says unknown, never empty", () => {
    const markup = render([agent({ name: "ops" })], { readFailed: true });
    expect(markup).toContain("Unknown: the roster could not be read.");
    expect(markup).not.toContain("No agents in this lane.");
  });
});
