// The status orb's judgement: UNKNOWN for anything that could not be read,
// never OK (CLAUDE.md invariant 7). Ported from the standalone node tests.
import { describe, expect, it } from "vitest";
import { classifyHealth, HEALTH_URL, HEARTBEAT_MAX_S, readHealth, type HealthReading } from "./health";

const hb = (c: unknown, o: unknown): HealthReading =>
  ({ status: 200, body: { ok: true, heartbeat: { cycle_age_s: c, observation_age_s: o } } });

describe("health", () => {
  it("reads ATLAS's own origin", () => {
    expect(HEALTH_URL).toBe("/healthz");
  });

  it("a fresh heartbeat is OK", () => {
    expect(classifyHealth(hb(4, 246)).state).toBe("OK");
  });

  it("anything unreadable is UNKNOWN, never OK", () => {
    const cases: Array<HealthReading | undefined> = [
      undefined,
      { error: "unreachable" },
      { status: 502, body: null },
      { status: 200, body: null },
      { status: 200, body: { ok: false, heartbeat: { cycle_age_s: 1, observation_age_s: 1 } } },
      { status: 200, body: { ok: true } },
      hb(null, 3),
      hb("4", 3),
      hb(-1, 3),
    ];
    for (const c of cases) expect(classifyHealth(c).state, JSON.stringify(c)).toBe("UNKNOWN");
  });

  it("an age over the limit is STALE, and stale wins over a null partner", () => {
    expect(classifyHealth(hb(3, HEARTBEAT_MAX_S)).state).toBe("OK");
    expect(classifyHealth(hb(3, HEARTBEAT_MAX_S + 1)).state).toBe("STALE");
    expect(classifyHealth(hb(null, 5000)).state).toBe("STALE");
  });

  it("a fetch that throws, or a body that is not JSON, reads as UNKNOWN", async () => {
    const reading = await readHealth(async () => { throw new TypeError("Failed to fetch"); });
    expect(classifyHealth(reading).state).toBe("UNKNOWN");
    const bad = await readHealth(async () => ({ status: 200, json: async () => { throw new Error("x"); } }));
    expect(classifyHealth(bad).state).toBe("UNKNOWN");
  });
});
