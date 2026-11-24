import { describe, expect, it } from "vitest";
import type { ModuleTile } from "../api";
import { healthSegments } from "./healthSegments";

const tile = (module: string, health: string, extra: Partial<ModuleTile> = {}): ModuleTile => ({
  module, title: module, health, checks: [], last_check_at: null, next_check_at: null,
  enabled: true, latest_finding: null, ...extra,
});

describe("healthSegments", () => {
  it("orders ok, degraded, failing, unknown and drops empty ones", () => {
    const s = healthSegments(
      [tile("a", "ok"), tile("b", "ok"), tile("c", "failing"), tile("d", "unknown")], false);
    expect(s.map((x) => x.health)).toEqual(["ok", "failing", "unknown"]);
    expect(s.map((x) => x.count)).toEqual([2, 1, 1]);
  });
  it("labels carry the word", () => {
    const s = healthSegments([tile("a", "ok"), tile("b", "degraded")], false);
    expect(s[0]!.label).toMatch(/OK/);
    expect(s[1]!.label).toMatch(/Degraded/);
  });
  it("null or failed read is one unknown segment, never OK", () => {
    for (const s of [healthSegments(null, false), healthSegments([tile("a", "ok")], true)]) {
      expect(s).toHaveLength(1);
      expect(s[0]!.health).toBe("unknown");
      expect(s[0]!.label).toBe("Unknown: checks not read");
    }
  });
  it("nothing readable is unknown, and set-aside modules are not counted OK", () => {
    const s = healthSegments([tile("a", "unknown", { setup: "not set up" })], false);
    expect(s.every((x) => x.health === "unknown")).toBe(true);
    expect(s.some((x) => /OK/.test(x.label))).toBe(false);
  });
  it("an unknown module is never labelled OK", () => {
    const s = healthSegments([tile("a", "unknown")], false);
    expect(s).toHaveLength(1);
    expect(s[0]!.label).toMatch(/Unknown/);
    expect(s[0]!.label).not.toMatch(/\bOK\b/);
  });
});
