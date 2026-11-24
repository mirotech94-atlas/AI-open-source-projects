import { describe, expect, it } from "vitest";
import { SCREEN_ROUTES, type SystemCheck } from "../api";
import {
  CHECKLIST_MAX_WAIT_MS,
  carryFocus,
  checklistDelayMs,
  focusUrl,
  graphLayout,
  owedAfter,
  planFocus,
} from "./focus";

describe("carryFocus", () => {
  const flycheap = { screen: "/status", module: "flycheap" };

  it("keeps the conversation's focus through a turn that names nothing", () => {
    // "how is flycheap doing", then "thanks, atlas": the card stays.
    const kept = carryFocus(flycheap, null);
    expect(kept).toBe(flycheap);
    expect(carryFocus(kept, undefined)).toBe(flycheap);
  });

  it("takes a new reply's focus as a fresh object", () => {
    const reply = { screen: "/fleet" };
    const next = carryFocus(flycheap, reply);
    expect(next).toEqual(reply);
    expect(next).not.toBe(reply);
    // The same command twice is two objects, so it navigates twice.
    expect(carryFocus(next, reply)).not.toBe(next);
  });
});

describe("owedAfter", () => {
  it("a module focus owes its screen on close", () => {
    expect(owedAfter(null, { screen: "/status", module: "flycheap" }))
      .toBe("/status?focus=flycheap");
  });

  it("no focus leaves what is owed alone", () => {
    expect(owedAfter("/status?focus=flycheap", null)).toBe("/status?focus=flycheap");
  });

  it("a newer focus in the conversation replaces it", () => {
    expect(owedAfter("/status?focus=flycheap", { screen: "/status", module: "guardian" }))
      .toBe("/status?focus=guardian");
    // A view command navigates now and leaves nothing owed.
    expect(owedAfter("/status?focus=flycheap", { screen: "/fleet" })).toBeNull();
  });
});

describe("planFocus", () => {
  it("does nothing without a focus", () => {
    expect(planFocus(null)).toEqual({ now: null, onDismiss: null, card: null });
    expect(planFocus(undefined)).toEqual({ now: null, onDismiss: null, card: null });
  });

  it("navigates a view command now, as before", () => {
    expect(planFocus({ screen: "/core" })).toEqual(
      { now: "/core", onDismiss: null, card: null });
    expect(planFocus({ screen: "/knowledge",
                       open: { kind: "report", id: 17, title: "Guardian audit" } }).now)
      .toBe("/knowledge?open=report%3A17");
  });

  it("brings the fleet up now for a system check", () => {
    expect(planFocus({ screen: "/fleet" })).toEqual(
      { now: "/fleet", onDismiss: null, card: null });
  });

  it("shows a module's card beside the answer and navigates on dismiss", () => {
    expect(planFocus({ screen: "/status", module: "flycheap" })).toEqual({
      now: null, onDismiss: "/status?focus=flycheap", card: "flycheap",
    });
  });

  it("never navigates to a screen the SPA does not have", () => {
    expect(planFocus({ screen: "https://evil.example" })).toEqual(
      { now: null, onDismiss: null, card: null });
    expect(planFocus({ screen: "/admin" }).now).toBeNull();
    // The card may still show; the navigation does not happen.
    expect(planFocus({ screen: "//evil", module: "flycheap" })).toEqual(
      { now: null, onDismiss: null, card: "flycheap" });
  });

  it("never trusts a module name that is not a plain identifier", () => {
    expect(planFocus({ screen: "/status", module: "../audit" })).toEqual(
      { now: "/status", onDismiss: null, card: null });
    expect(focusUrl({ screen: "/status", module: "a b" })).toBe("/status");
  });

  it("every screen in the table is an absolute SPA path", () => {
    for (const screen of SCREEN_ROUTES) expect(screen).toMatch(/^\/[a-z]*$/);
  });
});

function check(agents: number, modules: number): SystemCheck {
  return {
    as_of: "2026-09-24T21:30:00Z",
    agents: Array.from({ length: agents }, (_, i) => ({
      name: `a${i}`, title: `A${i}`, online: true, reason: null })),
    modules: Array.from({ length: modules }, (_, i) => ({
      name: `m${i}`, title: `M${i}`, health: "ok", checks_ok: 1, checks_total: 1,
      unknown: 0, degraded: 0, failing: 0, enabled: true, reason: null,
      not_measured: null })),
    summary: { ok: modules, degraded: 0, failing: 0, unknown: 0, total: modules,
               not_loaded: 0 },
    oldest_reading: null,
    unreadable: null,
    line: "All clear.",
  };
}

describe("checklistDelayMs", () => {
  it("waits one 60 ms stagger per row plus the last flip", () => {
    expect(checklistDelayMs(check(3, 2))).toBe(5 * 60 + 250);
  });

  it("is capped so a large estate does not hold the answer back", () => {
    expect(checklistDelayMs(check(40, 40))).toBe(CHECKLIST_MAX_WAIT_MS);
  });

  it("does not wait when there is nothing to draw", () => {
    expect(checklistDelayMs(null)).toBe(0);
    expect(checklistDelayMs(check(0, 0))).toBe(0);
  });
});

describe("graphLayout", () => {
  it("puts the front door (inbox) at the centre and the rest around it", () => {
    const { centre, nodes } = graphLayout(["inbox", "qa", "content"], 200);
    expect(centre).toBe("inbox");
    expect(nodes.map((n) => n.name)).toEqual(["qa", "content"]);
    // The first node sits straight above the centre.
    expect(nodes[0]).toEqual({ name: "qa", x: 100, y: 18 });
  });

  it("has no centre when there is no inbox, and a retired concierge is not one", () => {
    expect(graphLayout(["qa"], 200).centre).toBeNull();
    expect(graphLayout(["concierge", "qa"], 200).centre).toBeNull();
  });
});
