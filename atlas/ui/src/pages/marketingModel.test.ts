import { describe, expect, it } from "vitest";
import type { Marketing, MarketingExperiment } from "../api";
import { deltaText, lanes, panelView, planSummary } from "./marketingModel";

function board(over: Partial<Marketing> = {}): Marketing {
  return {
    as_of: "2026-09-24T09:00:00+00:00",
    watch_only: true,
    hooked_platforms: ["youtube"],
    unhooked_platforms: ["instagram", "tiktok"],
    hook_note: "Only youtube can close an experiment.",
    channels: [], experiments: [], playbook: [], ideas: [], plan: [],
    filing: { filed: 0, refused: 0, reasons: [], at: null, note: "none" },
    unavailable: { channels: null, experiments: null, playbook: null, ideas: null, plan: null },
    ...over,
  };
}

describe("panelView", () => {
  it("is UNKNOWN, never an empty board, when the endpoint could not be read", () => {
    const view = panelView(null, "HTTP 502", "experiments");
    expect(view.kind).toBe("unknown");
    expect(view.kind === "unknown" && view.reason).toContain("502");
  });

  it("is UNKNOWN for a section the server says it could not read", () => {
    const data = board({
      unavailable: { channels: null, experiments: "RuntimeError: locked",
                     playbook: null, ideas: null, plan: null },
    });
    expect(panelView(data, null, "experiments")).toEqual(
      { kind: "unknown", reason: "RuntimeError: locked" });
    expect(panelView(data, null, "playbook")).toEqual({ kind: "rows" });
  });

  it("keeps drawing the last good read when a refresh fails", () => {
    expect(panelView(board(), "HTTP 502", "ideas")).toEqual({ kind: "rows" });
  });

  it("is still reading before the first answer", () => {
    expect(panelView(null, null, "plan")).toEqual({ kind: "reading" });
  });
});

describe("lanes", () => {
  it("puts each experiment in the lane the server chose", () => {
    const e = (id: number, stage: MarketingExperiment["stage"]) =>
      ({ id, stage } as MarketingExperiment);
    const out = lanes([e(1, "draft"), e(2, "measured"), e(3, "awaiting"), e(4, "published")]);
    expect(out.draft.map((x) => x.id)).toEqual([1]);
    expect(out.awaiting.map((x) => x.id)).toEqual([3]);
    expect(out.published.map((x) => x.id)).toEqual([4]);
    expect(out.measured.map((x) => x.id)).toEqual([2]);
  });
});

describe("deltaText", () => {
  it("signs the change and says nothing when nothing was compared", () => {
    expect(deltaText(64)).toBe("+64%");
    expect(deltaText(-30.4)).toBe("−30%");
    expect(deltaText(0.2)).toBe("±0%");
    expect(deltaText(null)).toBeNull();
    expect(deltaText(Number.POSITIVE_INFINITY)).toBeNull();
  });
});

describe("planSummary", () => {
  const item = (id: number, expired: boolean) => ({
    id, action: "youtube.set_visibility", reason: "r", channel: "flycheap",
    experiment_id: null, created_at: null, expires_at: "2026-09-24T09:00:00+00:00", expired,
  });

  it("never lists an expired proposal as waiting on him", () => {
    const s = planSummary([item(1, true), item(2, true)]);
    expect(s.live).toEqual([]);
    expect(s.expired.map((p) => p.id)).toEqual([1, 2]);
    expect(s.heading).toBe("Nothing waiting on you; 2 expired.");
  });

  it("counts the live ones and names the expired ones apart", () => {
    const s = planSummary([item(1, false), item(2, true)]);
    expect(s.live.map((p) => p.id)).toEqual([1]);
    expect(s.heading).toBe("1 waiting on you; 1 expired.");
  });

  it("says plainly when there is nothing at all", () => {
    expect(planSummary([]).heading).toBe("No publish decisions waiting on you.");
  });
});
