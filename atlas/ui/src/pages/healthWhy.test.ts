import { describe, expect, it } from "vitest";
import type { CheckSummary, ModuleTile } from "../api";
import { clip, readTime, whyList } from "./healthWhy";
import { overall, statusPill } from "./commandModel";

const chk = (name: string, health: string, detail = "", at: string | null = "2026-11-12T10:00:00Z",
             extra: Partial<CheckSummary> = {}): CheckSummary => ({
  name, health, detail, observed_at: at, next_run_at: null, interval_s: 300, ...extra,
});
const mod = (module: string, health: string, checks: CheckSummary[],
             extra: Partial<ModuleTile> = {}): ModuleTile => ({
  module, title: module, health, checks, last_check_at: null, next_check_at: null,
  enabled: true, latest_finding: null, ...extra,
});

describe("whyList", () => {
  const mods = [
    mod("academy", "failing", [chk("delivery", "failing", "ACADEMY_STRIPE_READ_KEY unset; files missing"),
                               chk("catalog", "ok")]),
    mod("backups", "failing", [chk("offbox", "failing", "off-box copy not done")]),
    mod("mail", "degraded", [chk("watch_active", "degraded", "Gmail re-auth 400")]),
    mod("seo", "unknown", [chk("rank", "unknown", "no reading yet", null),
                           chk("pages", "unknown", "timed out", "2026-11-12T09:00:00Z", { stale: true })]),
    mod("comms", "unknown", [chk("slack", "unknown", "token unset")]),
    mod("fine", "ok", [chk("a", "ok")]),
  ];

  it("lists failing first, then degraded; unknown grouped by module", () => {
    const w = whyList(mods);
    expect(w.attention.map((r) => `${r.module}.${r.check}:${r.health}`)).toEqual([
      "academy.delivery:failing", "backups.offbox:failing", "mail.watch_active:degraded"]);
    expect(w.unknown.map((g) => [g.module, g.rows.length])).toEqual([["seo", 2], ["comms", 1]]);
    expect(w.unknownCount).toBe(3);
  });

  it("never hides or downgrades a real failing check", () => {
    const w = whyList(mods);
    expect(w.attention.filter((r) => r.health === "failing")).toHaveLength(2);
    expect(w.attention.every((r) => r.health !== "unknown")).toBe(true);
  });

  it("agrees with the pill: same modules in, same attention and unknown modules out", () => {
    const o = overall(mods);
    const w = whyList(mods);
    expect(new Set(w.attention.map((r) => r.module)).size).toBe(o.failing + o.degraded);
    expect(w.unknown.length).toBe(o.unknown);
    expect(statusPill(o, false).text).toBe("3 need attention · 2 unknown");
  });

  it("a never-read check says so; a stale one is flagged", () => {
    const rows = whyList(mods).unknown[0]!.rows;
    expect(rows[0]!.readAt).toBeNull();
    expect(readTime(rows[0]!.readAt)).toBe("never read");
    expect(rows[1]!.stale).toBe(true);
  });

  it("leaves set-aside checks out and counts them", () => {
    const w = whyList([
      mod("x", "unknown", [chk("a", "unknown", "", null, { state: { kind: "not_set_up", label: "not set up" } })]),
      mod("y", "unknown", [chk("b", "unknown")], { setup: "switched off" }),
    ]);
    expect(w.unknown).toEqual([]);
    expect(w.setAside).toBe(2);
  });

  it("a counted module with no check rows is still named", () => {
    const w = whyList([mod("z", "failing", [], { latest_finding: { id: 1, title: "down", severity: 3, at: "" } })]);
    expect(w.attention).toHaveLength(1);
    expect(w.attention[0]!.detail).toBe("down");
  });

  it("a failed read is UNKNOWN 'checks unreachable', never the last OK", () => {
    expect(statusPill(overall(null), true)).toEqual({ health: "unknown", text: "Unknown: checks unreachable" });
  });

  it("clips detail to 100 characters", () => {
    expect(clip("x".repeat(250))).toBe(`${"x".repeat(100)}…`);
    expect(clip("  ")).toBe("no detail given");
    expect(whyList(null).attention).toEqual([]);
  });
});
