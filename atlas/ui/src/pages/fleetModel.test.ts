import { describe, expect, it } from "vitest";
import type { AgentSummary } from "../api";
import { checkReading, modelState, stateAttr, status } from "./fleetModel";

const agent = (over: Partial<AgentSummary> = {}): AgentSummary => ({
  name: "ops", title: "Ops", display: "Ops", brief: "", need: null,
  available: true, providers: [], tools: 3, last_run_at: null,
  last_outcome: null, runs_24h: 0, model: null, model_at: null, handoff: [],
  memory_scope: [], ready: true, reasons: [], group: "functions",
  next_shift_at: null, ...over,
});

describe("modelState tells 'never ran' apart from 'ran, answered nothing' (M2)", () => {
  it("unrun: no row at all", () => {
    expect(modelState({ model: null, model_at: null })).toBe("unrun");
  });

  it("silent: a row exists (model_at set) but nothing answered", () => {
    expect(modelState({ model: null, model_at: "2026-09-24T09:00:00Z" }))
      .toBe("silent");
  });

  it("answered: a model actually answered", () => {
    expect(modelState({ model: "anthropic:claude-sonnet-4-5",
                        model_at: "2026-09-24T09:00:00Z" })).toBe("answered");
  });
});

describe("a stale or failed read renders UNKNOWN on Fleet", () => {
  it("a stale OK from the server is UNKNOWN, never the raw last entry", () => {
    const check = {
      current: { health: "unknown", stale: true, at: "2026-09-24T01:00:00Z" },
      history: [{ at: "2026-09-24T01:00:00Z", health: "ok", value: null }],
    };
    expect(checkReading(check)).toEqual(
      { health: "unknown", at: "2026-09-24T01:00:00Z", stale: true });
  });

  it("a current reading is read as the server says", () => {
    expect(checkReading({ current: { health: "OK", stale: false, at: null } }))
      .toEqual({ health: "ok", at: null, stale: false });
    expect(checkReading({ current: { health: "sideways", stale: false, at: null } })
      .health).toBe("unknown");
    expect(checkReading({}).health).toBe("unknown");
  });

  it("every card is UNKNOWN, grey and dashed, when the roster read failed", () => {
    const now = Date.parse("2026-09-24T12:00:00Z");
    const busy = agent({ last_run_at: "2026-09-24T11:59:30Z" });
    expect(status(busy, false, now)).toBe("WORKING");
    expect(status(busy, true, now)).toBe("UNKNOWN");
    expect(stateAttr("UNKNOWN")).toBe("unknown");
    expect(stateAttr(status(agent({ available: false }), false, now))).toBe("unknown");
    expect(stateAttr(status(agent(), false, now))).toBe("available");
  });
});
