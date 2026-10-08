import { describe, expect, it } from "vitest";
import { GROUP_ORDER, TABS, redirectForMode, visibleTabs } from "../src/shell/nav";

// The redesign's hardest rule: not a single tab added, removed or renamed
// BY ACCIDENT. Adding one is a product decision, and this file is where it
// is recorded: "Live connections" (2026-09-27, Miro's request -- a live view
// of what devices connect to, directly under DNS filter) is the one row added
// since the redesign froze the list at fourteen.
// nav.tsx stays the one source of truth for order and grouping, `key` values
// go to the hub as ChatRequest.tab, and the flat children must survive
// because redirectForMode, the mobile More sheet and the palette read them.

const EXPECTED: Record<string, string[]> = {
  WATCH: ["Overview", "Inbox", "Activity"],
  NETWORK: ["Assets", "Topology", "Security", "Updates", "DNS filter", "Live connections"],
  RECORD: ["History", "Reports"],
  ACCOUNT: ["Integrations", "Config", "Support", "Help & API"],
};
const KEYS = ["overview", "inbox", "activity", "assets", "topology", "security", "updates", "dns", "connections", "history", "reports", "integrations", "settings", "support", "help"];

describe("portal navigation invariants", () => {
  it("is exactly fifteen rows in four groups, in this order", () => {
    expect(TABS).toHaveLength(15);
    for (const group of GROUP_ORDER) {
      expect(TABS.filter((t) => t.group === group).map((t) => t.label)).toEqual(EXPECTED[group]);
    }
  });

  it("keeps every key the hub is sent", () => {
    expect(TABS.map((t) => t.key).sort()).toEqual([...KEYS].sort());
  });

  it("Reports and Config keep their flat children", () => {
    const reports = TABS.find((t) => t.key === "reports")!;
    const config = TABS.find((t) => t.key === "settings")!;
    expect(reports.flat).toBe(true);
    expect(reports.children?.map((c) => c.label)).toEqual(["Builder", "Library", "Schedule"]);
    expect(config.flat).toBe(true);
    expect(config.children).toHaveLength(13);
  });

  it("simple mode still hides the advanced Config sections", () => {
    const config = TABS.find((t) => t.key === "settings")!;
    const adv = config.children!.filter((c) => c.adv === true);
    expect(adv.length).toBeGreaterThanOrEqual(9);
    for (const c of adv) {
      const to = redirectForMode(c.path.split("?")[0], "", false);
      expect(to, `${c.path} should redirect in simple mode`).not.toBeNull();
    }
    // and a plain section does not
    expect(redirectForMode("/settings/profile", "", false)).toBeNull();
  });

  it("advanced mode shows all fifteen; simple mode shows a subset of them, never a stranger", () => {
    expect(visibleTabs(true).map((t) => t.key).sort()).toEqual([...KEYS].sort());
    const simple = visibleTabs(false).map((t) => t.key);
    expect(simple.length).toBeLessThanOrEqual(15);
    for (const k of simple) expect(KEYS).toContain(k);
  });
});
