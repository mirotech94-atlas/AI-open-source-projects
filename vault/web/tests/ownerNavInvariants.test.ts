import { describe, expect, it } from "vitest";
import { SECTIONS } from "../src/owner/OwnerShell";

/** The redesign handoff's hard rule for the owner console: fourteen rows in
 *  five groups, in this order, with these labels. A redesign may restyle the
 *  rail; it may not add, drop or rename a section. */
describe("owner console navigation invariants", () => {
  it("has exactly fourteen sections in five groups, in order", () => {
    expect(SECTIONS).toHaveLength(14);
    const groups = SECTIONS.map((s) => s.group).filter((g, i, a) => a.indexOf(g) === i);
    expect(groups).toEqual(["Operations", "Inventory", "Insight", "Estate", "Account"]);
  });

  it("keeps every label", () => {
    expect(SECTIONS.map((s) => s.label)).toEqual([
      "Overview", "Incidents", "Maintenance", "Audit log",
      "Assets", "Websites & DNS", "Certificates",
      "Reports",
      "Customers", "Sensors", "Access requests", "Support",
      "Settings", "Billing",
    ]);
  });

  it("keeps every route unique and under /owner", () => {
    const routes = SECTIONS.map((s) => s.to);
    expect(new Set(routes).size).toBe(routes.length);
    for (const r of routes) expect(r.startsWith("/owner")).toBe(true);
  });
});
