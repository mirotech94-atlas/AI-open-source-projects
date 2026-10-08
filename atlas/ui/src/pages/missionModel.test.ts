import { describe, expect, it } from "vitest";
import type { ModuleTile } from "../api";
import {
  agentsWords, coreConnected, estate, llmsWords, memoryWords, systemWords,
} from "./missionModel";

// What useFresh holds after a refresh FAILED: the old data, and an error.
function failed<T>(data: T) { return { data, error: "HTTP 502" }; }
function fresh<T>(data: T) { return { data, error: null }; }

const mod = (health: string): ModuleTile => ({
  module: "m", title: "M", health, checks: [], last_check_at: null,
  next_check_at: null, enabled: true, latest_finding: null,
});

describe("Mission tiles never keep an OK look after a failed read", () => {
  it("the estate is unknown when the overview could not be read", () => {
    expect(estate(failed({ modules: [mod("ok"), mod("ok")] })))
      .toEqual({ worst: "unknown", hurt: [], blind: 0 });
    expect(estate(fresh({ modules: [mod("ok"), mod("degraded")] })).worst)
      .toBe("degraded");
  });

  it("the core is not connected on the strength of an old read", () => {
    const llm = { needs: { reason: { connected: true } } };
    expect(coreConnected(failed(llm))).toBe(false);
    expect(coreConnected(fresh(llm))).toBe(true);
  });

  it("memory, agents and LLMs read unreadable, grey, after a failure", () => {
    for (const words of [
      memoryWords(failed({ total: 12 })),
      agentsWords(failed({ agents: [{ available: true }] })),
      llmsWords(failed({ vendors: ["anthropic"] })),
    ]) {
      expect(words).toEqual({ state: "unknown", value: "unreadable" });
    }
    expect(memoryWords(fresh({ total: 12 }))).toEqual({ state: "live", value: "12 stored" });
    expect(agentsWords(fresh({ agents: [{ available: true }, { available: false }] })))
      .toEqual({ state: "live", value: "1 available" });
  });

  it("the system tile is never Optimal from a stale read", () => {
    const sys = { cpu_percent: 10, memory: { percent: 20 }, disk: { percent: 30 } };
    expect(systemWords(failed(sys))).toEqual({ state: "unknown", value: "unreadable" });
    expect(systemWords(fresh(sys))).toEqual({ state: "live", value: "Optimal" });
    expect(systemWords(fresh({ ...sys, cpu_percent: null })))
      .toEqual({ state: "unknown", value: "CPU unread" });
  });
});
