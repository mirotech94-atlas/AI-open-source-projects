import { describe, expect, it } from "vitest";
import { autopilotLine, type AutopilotRead } from "./autopilotModel";

const hh = (s: string): string => `T(${s})`;
const base: AutopilotRead = {
  read_at: "2026-11-24T10:00:00Z", last_cycle_at: "2026-11-24T09:30:00Z",
  fixed: 4, waiting_on_miro: 2, held: 0,
  items: [{ subject: "s", cause: "c", fix: "f", outcome: "o" }], unread: [],
};

describe("autopilotLine", () => {
  it("normal", () => {
    const l = autopilotLine(base, hh);
    expect(l.text).toBe("Autopilot · last cycle T(2026-11-24T09:30:00Z) · fixed 4 · waiting on Miro 2");
    expect(l.unknown).toEqual([]);
    expect(l.items).toHaveLength(1);
  });
  it("null figures are unknown, never 0", () => {
    const l = autopilotLine({ ...base, fixed: null, waiting_on_miro: null }, hh);
    expect(l.text).toContain("fixed Unknown");
    expect(l.text).toContain("waiting on Miro Unknown");
    expect(l.text).not.toMatch(/(fixed|Miro) 0/);
    expect(l.unknown).toEqual(["fixed", "waiting"]);
    expect(l.items).toEqual([]);
  });
  it("never ran is unknown", () => {
    const l = autopilotLine({ ...base, last_cycle_at: null }, hh);
    expect(l.text).toContain("last cycle no cycle yet");
    expect(l.unknown).toContain("cycle");
  });
  it("held hidden when 0 or null, shown when >0", () => {
    expect(autopilotLine({ ...base, held: 0 }, hh).text).not.toContain("held");
    expect(autopilotLine({ ...base, held: null }, hh).text).not.toContain("held");
    expect(autopilotLine({ ...base, held: 3 }, hh).text).toContain("held 3");
  });
  it("items capped at 3 and only when waiting > 0", () => {
    const items = Array.from({ length: 5 }, (_, i) => ({ ...base.items[0]!, subject: `s${i}` }));
    expect(autopilotLine({ ...base, items }, hh).items).toHaveLength(3);
    expect(autopilotLine({ ...base, items, waiting_on_miro: 0 }, hh).items).toEqual([]);
  });
  it("off shows its count, Unknown when null, nothing when 0 or absent", () => {
    expect(autopilotLine({ ...base, off: 47 }, hh).text).toContain("· off 47");
    const u = autopilotLine({ ...base, off: null }, hh);
    expect(u.text).toContain("off Unknown");
    expect(u.text).not.toMatch(/off 0/);
    expect(u.unknown).toContain("off");
    expect(autopilotLine({ ...base, off: 0 }, hh).text).not.toContain("off");
    expect(autopilotLine(base, hh).text).not.toContain("off");
  });
});
