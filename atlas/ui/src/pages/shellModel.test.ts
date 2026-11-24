import { describe, expect, it } from "vitest";
import { overall } from "./commandModel";
import { healthMini, crumbOf } from "./shellModel";

const mod = (health: string, setup = false) =>
  ({ key: "k", name: "n", enabled: true, health, setup } as never);

describe("healthMini", () => {
  it("one line per state with its count and word, never 'operational'", () => {
    const o = overall([mod("ok"), mod("ok"), mod("degraded"), mod("unknown")] as never);
    const m = healthMini(o, false);
    expect(m.failed).toBe(false);
    expect(m.lines.map((l) => [l.health, l.count, l.word])).toEqual([
      ["ok", 2, "OK"], ["degraded", 1, "Degraded"], ["failing", 0, "Failing"],
      ["unknown", 1, "Unknown"]]);
    expect(JSON.stringify(m)).not.toMatch(/operational/i);
  });
  it("a failed read has no counts at all", () => {
    const m = healthMini(overall(null), true);
    expect(m.failed).toBe(true);
    expect(m.lines).toEqual([]);
  });
  it("never read (no modules) is unknown, not zero OK", () => {
    const m = healthMini(overall(null), false);
    expect(m.failed).toBe(true);
    expect(m.lines).toEqual([]);
  });
});

describe("crumbOf", () => {
  it("names the tab for the path", () => {
    expect(crumbOf("/")).toBe("Command Center");
    expect(crumbOf("/agency")).toBe("Agency");
    expect(crumbOf("/cameras/x")).toBe("Cameras");
    expect(crumbOf("/hud")).toBe("HUD");
  });
});
