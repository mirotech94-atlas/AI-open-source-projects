import { describe, expect, it } from "vitest";
import type { AgentSummary, Approvals, ModuleTile, Overview, Proposal } from "../api";
import { agencyGlance } from "./agencyGlance";

const AT = new Date("2026-11-23T10:00:00Z");
const agent = (name: string, status: string): AgentSummary =>
  ({ name, status } as unknown as AgentSummary);
const prop = (status: string): Proposal => ({ id: 1, status } as unknown as Proposal);
const tile = (health: string): ModuleTile => ({
  module: "m", title: "m", health, checks: [], last_check_at: null, next_check_at: null,
  enabled: true, latest_finding: null,
});
const agents = (list: AgentSummary[]) =>
  ({ data: { as_of: "2026-11-23T09:00:00Z", agents: list }, error: null, fetchedAt: AT });
const approvals = (list: Proposal[]) =>
  ({ data: { as_of: "2026-11-23T09:01:00Z", proposals: list, watch_only: true } as Approvals,
     error: null, fetchedAt: AT });
const overview = (mods: ModuleTile[]) =>
  ({ data: { as_of: "2026-11-23T09:02:00Z", modules: mods, pending_approvals: 0,
             watch_only: true } as Overview, error: null, fetchedAt: AT });
const failed = { data: null, error: "boom", fetchedAt: null };

describe("agencyGlance", () => {
  it("counts registered, failing, waiting and degraded checks with read times", () => {
    const g = agencyGlance({
      agents: agents([agent("inbox", "ok"), agent("sites", "failing"), agent("qa", "idle")]),
      approvals: approvals([prop("pending"), prop("approved"), prop("pending")]),
      overview: overview([tile("ok"), tile("degraded"), tile("failing")]),
    });
    expect(g.registered).toEqual({ value: 3, readAt: "2026-11-23T09:00:00Z" });
    expect(g.failing).toEqual({ value: 1, readAt: "2026-11-23T09:00:00Z" });
    expect(g.waiting).toEqual({ value: 2, readAt: "2026-11-23T09:01:00Z" });
    expect(g.degradedChecks).toEqual({ value: 2, readAt: "2026-11-23T09:02:00Z" });
  });
  it("retired agents are not counted", () => {
    const g = agencyGlance({
      agents: agents([agent("inbox", "ok"), agent("guardian", "failing")]),
      approvals: approvals([]), overview: overview([]),
    });
    expect(g.registered.value).toBe(1);
    expect(g.failing.value).toBe(0);
  });
  it("a failed read gives null for its figures, never 0", () => {
    const g = agencyGlance({ agents: failed, approvals: failed, overview: failed });
    for (const f of [g.registered, g.failing, g.waiting, g.degradedChecks]) {
      expect(f.value).toBeNull();
    }
  });
  it("an unknown agent status is not counted as failing nor hidden", () => {
    const g = agencyGlance({
      agents: agents([agent("inbox", "unknown")]), approvals: approvals([]),
      overview: overview([]),
    });
    expect(g.registered.value).toBe(1);
    expect(g.failing.value).toBe(0);
  });
  it("one failed read leaves the others standing", () => {
    const g = agencyGlance({
      agents: agents([agent("inbox", "ok")]), approvals: failed, overview: overview([tile("ok")]),
    });
    expect(g.registered.value).toBe(1);
    expect(g.waiting.value).toBeNull();
    expect(g.degradedChecks.value).toBe(0);
  });
  it("no figure is without a readAt key", () => {
    const g = agencyGlance({ agents: failed, approvals: failed, overview: failed });
    for (const f of Object.values(g)) expect("readAt" in f).toBe(true);
  });
});
