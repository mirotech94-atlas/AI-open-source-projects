import { describe, expect, it } from "vitest";
import type { AgentSummary } from "../api";
import { agentMapModel, connectionsFor, dotFor } from "./agentMapLayout";

function agent(over: Partial<AgentSummary> = {}): AgentSummary {
  return {
    name: "ops", title: "Ops", display: "Ops", brief: "infra", need: null,
    available: true, providers: [], tools: 3, last_run_at: null,
    last_outcome: null, runs_24h: 0, model: null, model_at: null,
    handoff: [], memory_scope: [], ready: true, reasons: [],
    group: "functions", next_shift_at: null,
    ...over,
  };
}

describe("dotFor -- the card's status dot", () => {
  it("ready, never run: the plain ready dot", () => {
    expect(dotFor({ ready: true, last_run_at: null })).toBe("ready");
  });

  it("ready and run inside the working window: working", () => {
    expect(dotFor({ ready: true, last_run_at: "2026-09-24T08:59:30Z" },
                  false, new Date("2026-09-24T09:00:00Z").getTime()))
      .toBe("working");
  });

  it("ready but run outside the working window: plain ready, not working", () => {
    expect(dotFor({ ready: true, last_run_at: "2026-09-24T08:00:00Z" },
                  false, new Date("2026-09-24T09:00:00Z").getTime()))
      .toBe("ready");
  });

  it("not ready: its own look, never a shade of ready", () => {
    expect(dotFor({ ready: false, last_run_at: null })).toBe("not-ready");
  });

  it("not ready even with a recent run -- readiness gates working, not the other way round", () => {
    expect(dotFor({ ready: false, last_run_at: "2026-09-24T08:59:30Z" },
                  false, new Date("2026-09-24T09:00:00Z").getTime()))
      .toBe("not-ready");
  });

  it("a failed roster read is unknown regardless of readiness", () => {
    expect(dotFor({ ready: true, last_run_at: null }, true)).toBe("unknown");
  });
});

describe("agentMapModel -- every roster agent placed exactly once", () => {
  it("puts the lead agent on top and everyone else in a lane", () => {
    const agents = [
      agent({ name: "concierge", group: "lead" }),
      agent({ name: "flycheap", group: "businesses" }),
      agent({ name: "ops", group: "functions" }),
    ];
    const model = agentMapModel(agents);
    expect(model.lead?.name).toBe("concierge");
    expect(model.businesses.map((a) => a.name)).toEqual(["flycheap"]);
    expect(model.functions.map((a) => a.name)).toEqual(["ops"]);
  });

  it("places every agent exactly once, whatever the input order", () => {
    const names = ["concierge", "flycheap", "guardian", "ops", "growth", "school"];
    const agents = names.map((name, i) =>
      agent({ name, group: i === 0 ? "lead" : i < 3 ? "businesses" : "functions" }));
    const shuffled = [agents[3]!, agents[0]!, agents[5]!, agents[1]!, agents[4]!, agents[2]!];
    const model = agentMapModel(shuffled);
    const placed = [
      ...(model.lead ? [model.lead.name] : []),
      ...model.businesses.map((a) => a.name),
      ...model.functions.map((a) => a.name),
    ];
    expect(placed.sort()).toEqual([...names].sort());
    // Exactly once: no name appears twice across the three slots.
    expect(new Set(placed).size).toBe(placed.length);
  });

  it("an unrecognised group lands in Functions rather than being dropped", () => {
    const agents = [agent({ name: "mystery", group: "something-new" as never })];
    const model = agentMapModel(agents);
    expect(model.functions.map((a) => a.name)).toEqual(["mystery"]);
  });

  it("no lead: every agent still lands in a lane", () => {
    const agents = [agent({ name: "ops", group: "functions" }),
                    agent({ name: "flycheap", group: "businesses" })];
    const model = agentMapModel(agents);
    expect(model.lead).toBeNull();
    expect(model.businesses).toHaveLength(1);
    expect(model.functions).toHaveLength(1);
  });

  it("each lane is sorted by name, regardless of input order", () => {
    const agents = [
      agent({ name: "growth", group: "functions" }),
      agent({ name: "analyst", group: "functions" }),
      agent({ name: "ops", group: "functions" }),
    ];
    const model = agentMapModel(agents);
    expect(model.functions.map((a) => a.name)).toEqual(["analyst", "growth", "ops"]);
  });
});

describe("connectionsFor -- no edges until something is selected", () => {
  const roster = [
    agent({ name: "concierge", handoff: ["ops", "inbox"], memory_scope: ["people"] }),
    agent({ name: "ops", handoff: [], memory_scope: ["infra"] }),
    agent({ name: "inbox", handoff: ["scheduler"], memory_scope: ["people", "threads"] }),
    agent({ name: "scheduler", handoff: [], memory_scope: ["calendar"] }),
  ];

  it("nothing selected: no connections at all", () => {
    const c = connectionsFor(null, roster);
    expect(c.handoff.size).toBe(0);
    expect(c.memory.size).toBe(0);
  });

  it("an unknown name: no connections", () => {
    const c = connectionsFor("ghost", roster);
    expect(c.handoff.size).toBe(0);
    expect(c.memory.size).toBe(0);
  });

  it("outgoing handoffs are connected", () => {
    const c = connectionsFor("concierge", roster);
    expect(c.handoff).toEqual(new Set(["ops", "inbox"]));
  });

  it("incoming handoffs are connected too", () => {
    const c = connectionsFor("scheduler", roster);
    expect(c.handoff).toEqual(new Set(["inbox"]));
  });

  it("shared memory namespaces are connected, symmetrically", () => {
    const c = connectionsFor("concierge", roster);
    expect(c.memory).toEqual(new Set(["inbox"]));
    expect(connectionsFor("inbox", roster).memory).toEqual(new Set(["concierge"]));
  });

  it("a handoff target the roster does not hold is dropped, not connected to nowhere", () => {
    const withGhost = [agent({ name: "a", handoff: ["ghost"] })];
    expect(connectionsFor("a", withGhost).handoff.size).toBe(0);
  });

  it("never connects an agent to itself", () => {
    const selfRef = [agent({ name: "a", handoff: ["a"], memory_scope: ["x"] })];
    const c = connectionsFor("a", selfRef);
    expect(c.handoff.has("a")).toBe(false);
    expect(c.memory.has("a")).toBe(false);
  });
});
