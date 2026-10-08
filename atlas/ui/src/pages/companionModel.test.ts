import { describe, expect, it } from "vitest";
import type { ModuleTile } from "../api";
import { companionTiles, headline, ladderWords } from "./companionModel";

function mod(module: string, checks: ModuleTile["checks"]): ModuleTile {
  return {
    module, title: module, health: "ok", checks, last_check_at: null,
    next_check_at: null, enabled: true, latest_finding: null,
  };
}

const at = "2026-09-28T18:49:00Z";

describe("companionTiles", () => {
  it("shows each check's own headline and read time", () => {
    const tiles = companionTiles([mod("markets", [{
      name: "basket", health: "ok", observed_at: at, next_run_at: null, interval_s: 3600,
      detail: "basket +0.06% on the day (equal weight, 12 stocks); paper $100 basket",
    }])]);
    const markets = tiles.find((t) => t.key === "markets.basket");
    expect(markets?.value).toBe("basket +0.06% on the day (equal weight, 12 stocks)");
    expect(markets?.state).toBe("live");
    expect(markets?.readAt?.toISOString()).toBe("2026-09-28T18:49:00.000Z");
  });

  it("a missing check is unknown, never live", () => {
    const tiles = companionTiles([]);
    expect(tiles.every((t) => t.state === "unknown")).toBe(true);
    expect(tiles.every((t) => t.readAt === null)).toBe(true);
  });

  it("a stale reading is unknown even if it said ok", () => {
    const [reels] = companionTiles([mod("content", [{
      name: "reel_legs", health: "ok", observed_at: at, next_run_at: null,
      interval_s: 21600, detail: "all legs posted", stale: true,
    }])]);
    expect(reels?.state).toBe("unknown");
  });

  it("a failing check warns", () => {
    const tiles = companionTiles([mod("shop", [{
      name: "sales_7d", health: "failing", observed_at: at, next_run_at: null,
      interval_s: 21600, detail: "Stripe returned 401",
    }])]);
    expect(tiles.find((t) => t.key === "shop.sales_7d")?.state).toBe("warn");
  });
});

describe("headline", () => {
  it("cuts at the first clause and caps the length", () => {
    expect(headline("a; b")).toBe("a");
    expect(headline("x".repeat(100), 10)).toBe(`${"x".repeat(9)}…`);
  });
});

describe("ladderWords", () => {
  it("says each rung, and names what did not resolve", () => {
    expect(ladderWords({ watches: 3, acts_alone: 1, asks_first: 2, never: 0, unknown: 1 }))
      .toBe("watches 3 · does 1 by itself · asks you before 2 · 1 unresolved");
  });
  it("is unknown, not zero, when the server sent none", () => {
    expect(ladderWords(null)).toBe("autonomy unknown");
  });
});
