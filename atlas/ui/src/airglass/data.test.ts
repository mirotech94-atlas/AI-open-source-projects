// The data panels read real numbers or say they could not; UNKNOWN is never
// OK; "explain" speaks only what was just read, with its time.
import { describe, expect, it } from "vitest";
import type { AgentSummary, ModuleTile, Overview, Tasks } from "../api";
import { explainAdded, explainProduct, MODEL_ABOUT, PRODUCTS } from "./catalog";
import { agentsPanel, explainPanel, healthPanel, salesPanel, tasksPanel, type PanelData } from "./data";

const mod = (m: Partial<ModuleTile>): ModuleTile => ({
  module: "x", title: "X", health: "ok", checks: [], last_check_at: null, next_check_at: null,
  enabled: true, latest_finding: null, ...m,
});
const overview = (modules: ModuleTile[]): Overview =>
  ({ as_of: "2026-10-06T16:40:00Z", modules, pending_approvals: 0, watch_only: true });

describe("health panel", () => {
  it("never shows a failed or unknown module as OK", () => {
    const p = healthPanel(overview([
      mod({ module: "a", title: "Alpha", health: "ok" }),
      mod({ module: "b", title: "Beta", health: "failing" }),
      mod({ module: "c", title: "Gamma", health: "" }),
      mod({ module: "d", title: "Delta", health: "ok", setup: "not set up" }),
      mod({ module: "e", title: "Off", enabled: false }),
    ]));
    expect(p.rows.map((r) => `${r.label}:${r.state}:${r.value}`)).toEqual([
      "Alpha:ok:OK", "Beta:fail:FAILING", "Gamma:unknown:UNKNOWN", "Delta:unknown:not set up",
    ]);
    expect(p.summary).toBe("1 OK, 1 need attention, 2 unknown");
    expect(p.readAt?.toISOString()).toBe("2026-10-06T16:40:00.000Z");
  });
});

describe("sales panel", () => {
  const check = (c: object): Overview => overview([mod({ module: "shop", checks: [{
    name: "sales_7d", health: "ok", detail: "$120 from 3 orders; more", observed_at: "2026-10-06T16:30:00Z",
    next_run_at: null, interval_s: 900, ...c,
  }] })]);

  it("is the check's own headline, with the check's read time", () => {
    const p = salesPanel(check({}));
    expect(p.rows[0]).toEqual({ label: "Last 7 days", value: "$120 from 3 orders", state: "ok" });
    expect(p.readAt?.toISOString()).toBe("2026-10-06T16:30:00.000Z");
  });

  it("a stale reading is unknown, and a missing check has no figure", () => {
    expect(salesPanel(check({ stale: true })).rows[0]?.state).toBe("unknown");
    const none = salesPanel(overview([]));
    expect(none.rows).toEqual([]);
    expect(none.summary).toMatch(/not running/);
  });
});

describe("tasks and agents panels", () => {
  it("count what waits and who ran", () => {
    const t: Tasks = { as_of: "2026-10-06T16:40:00Z", pending: 2, jobs: [], research: [],
      proposals: [{ id: 1, action: "mac.open_app", reason: "Open Notes; because", tier: 2,
        created_at: null, expires_at: null, finding: null }] };
    const p = tasksPanel(t);
    expect(p.summary).toBe("2 things wait on you.");
    expect(p.rows[3]).toEqual({ label: "mac.open_app", value: "Open Notes", state: "plain" });
    const a = [{ name: "scout", title: "Scout", display: "Scout", runs_24h: 3, last_outcome: "ok", ready: true },
      { name: "idle", title: "Idle", display: "Idle", runs_24h: 0, last_outcome: null, ready: true }] as AgentSummary[];
    const g = agentsPanel(a, "2026-10-06T16:40:00Z");
    expect(g.summary).toBe("1 agent ran in the last 24 hours.");
    expect(g.rows[0]?.value).toBe("3 runs today · ok");
  });
});

describe("explain", () => {
  it("a failed read is said as a failure, never as a number", () => {
    const failed: PanelData = { kind: "sales", title: "Store sales", summary: "", rows: [],
      readAt: null, error: "could not be read (503)" };
    expect(explainPanel(failed)).toBe("Store sales: I couldn't read it just now, so I won't guess a number.");
  });

  it("a read panel is said with its read time", () => {
    const p = healthPanel(overview([mod({ title: "Beta", health: "failing" })]));
    const words = explainPanel(p);
    expect(words).toMatch(/^Health, read at /);
    expect(words).toMatch(/Beta is failing/);
  });

  it("models and products use fixed words only", () => {
    expect(Object.keys(MODEL_ABOUT).sort()).toEqual(["atlas", "crystal", "globe", "knot"]);
    expect(explainAdded("chair")).toMatch(/All I know about it is its file name/);
    const p = PRODUCTS.find((x) => x.slug === "safe-ai-operator");
    expect(p).toBeDefined();
    if (p) expect(explainProduct(p)).toBe(`${p.title}. A course from ATLAS Academy. ${p.summary}`);
  });

  it("bundles a cover for every product whose catalog entry names one", () => {
    expect(PRODUCTS.length).toBeGreaterThanOrEqual(7);
    const kit = PRODUCTS.find((x) => x.slug === "claude-obsidian-vault-kit");
    expect(kit?.cover).toBeNull();
    for (const p of PRODUCTS.filter((x) => x.slug !== "claude-obsidian-vault-kit")) {
      expect(p.cover, p.slug).toBeTruthy();
    }
  });
});
