/** Four tabs, and every old path lands on one of them. */
import { describe, expect, it } from "vitest";
import { REDIRECTS, TABS, nextTab } from "./nav";

describe("nav", () => {
  it("is exactly two tabs, no HUD tab", () => {
    expect(TABS).toEqual([{ to: "/", label: "Command Center" }, { to: "/agency", label: "Agency" }]);
  });

  it("old paths redirect to a tab", () => {
    // What moved off the short Command Center (2026-10-29) opens on Agency.
    for (const p of ["/fleet", "/agents", "/agency/room", "/tasks", "/calendar", "/leads",
                     "/activity", "/reports"]) {
      expect(REDIRECTS[p], p).toBe("/agency");
    }
    for (const p of ["/command", "/core", "/status", "/marketing",
                     "/workflows", "/tools", "/knowledge", "/memory", "/conversations"]) {
      expect(REDIRECTS[p], p).toBe("/");
    }
    const tabs: string[] = TABS.map((t) => t.to);
    expect(Object.values(REDIRECTS).every((to) => tabs.includes(to))).toBe(true);
  });

  it("swipe walks the two tabs", () => {
    expect(nextTab("/", "left")).toBe("/agency");
    expect(nextTab("/agency", "left")).toBe("/");
    expect(nextTab("/", "right")).toBe("/agency");
    expect(nextTab("/agency", "right")).toBe("/");
    expect(TABS.map((t) => t.to)).toContain(nextTab("/nowhere", "left"));
  });
});
