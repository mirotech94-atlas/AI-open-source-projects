import { describe, expect, it } from "vitest";
import {
  isoBar, latestLeads, pipelineBars, scaleHeights, shiftBars,
} from "./chartModel";
import type { Lead, PipelineRead } from "../api";
import type { RosterAgent } from "./opsFloorModel";

const AT = "2026-10-30T09:00:00Z";
const ok = { state: "ok", read_at: AT, reason: null } as const;
const row = (stage: string | null, kind: "site" | "lead" = "lead") =>
  ({ client: "c", kind, stage, next_step: null, reason: null, read_at: AT });
const pipe = (rows: PipelineRead["rows"], over: Partial<PipelineRead> = {}): PipelineRead =>
  ({ rows, sites: ok, leads: ok, read_at: AT, ...over } as PipelineRead);

describe("pipelineBars", () => {
  it("is unknown when nothing was read", () => {
    expect(pipelineBars(null).state).toBe("unknown");
  });
  it("is unknown when either source block is unknown, never a partial count", () => {
    const r = pipelineBars(pipe([row("new")], {
      leads: { state: "unknown", read_at: null, reason: "db down" },
    }));
    expect(r).toEqual({ state: "unknown", reason: "db down" });
  });
  it("a real empty result is a real zero with its read time", () => {
    expect(pipelineBars(pipe([]))).toEqual({
      state: "ok", bars: [], total: 0, unstaged: 0, readAt: AT,
    });
  });
  it("counts rows per stage, biggest first, and keeps unstaged apart", () => {
    const r = pipelineBars(pipe([row("new"), row("won"), row("new"), row(null), row(" New ")]));
    expect(r).toMatchObject({ state: "ok", total: 5, unstaged: 1, readAt: AT });
    if (r.state === "ok") {
      expect(r.bars).toEqual([{ stage: "new", count: 3 }, { stage: "won", count: 1 }]);
    }
  });
  it("folds stages past the sixth into 'other'", () => {
    const rows = ["a", "b", "c", "d", "e", "f", "g", "h"].map((s) => row(s));
    const r = pipelineBars(pipe(rows));
    if (r.state !== "ok") throw new Error("ok expected");
    expect(r.bars).toHaveLength(6);
    expect(r.bars[5]).toEqual({ stage: "other", count: 3 });
  });
});

function agent(over: Partial<RosterAgent>): RosterAgent {
  return {
    name: "scout", role: "Scout", status: "ok", shift_state: "done", last_shift_at: AT,
    last_shift_outcome: null, last_shift_outcome_detail: null, next_shift_at: null,
    attempts_today: 2, today_output: 1, alert_unrouted: false, read_at: AT, ...over,
  };
}

describe("shiftBars", () => {
  it("is unknown without a roster read", () => {
    expect(shiftBars(null).state).toBe("unknown");
  });
  it("an unread count is an unknown bar, never zero", () => {
    const r = shiftBars([agent({ name: "a", attempts_today: null }),
      agent({ name: "b", attempts_today: 0 }), agent({ name: "c", attempts_today: 3 })]);
    if (r.state !== "ok") throw new Error("ok expected");
    expect(r.bars).toEqual([
      { name: "c", count: 3 }, { name: "b", count: 0 }, { name: "a", count: null },
    ]);
    expect(r.readAt).toBe(AT);
  });
  it("an empty roster is a real empty chart", () => {
    expect(shiftBars([])).toMatchObject({ state: "ok", bars: [] });
  });
});

describe("scaleHeights", () => {
  it("scales to the max, null stays null, all-zero stays flat", () => {
    expect(scaleHeights([4, 2, null], 100)).toEqual([100, 50, null]);
    expect(scaleHeights([0, 0], 100)).toEqual([0, 0]);
    expect(scaleHeights([], 100)).toEqual([]);
  });
  it("gives a non-zero count at least a visible sliver", () => {
    expect(scaleHeights([1000, 1], 100)[1]).toBeGreaterThanOrEqual(2);
  });
});

describe("isoBar", () => {
  it("builds top, left and right faces that share the front edge", () => {
    const b = isoBar(10, 100, 20, 10, 30);
    expect(b.front).toBe("10,100 30,100 30,70 10,70");
    expect(b.top).toBe("10,70 30,70 40,65 20,65");
    expect(b.side).toBe("30,100 40,95 40,65 30,70");
  });
  it("zero height is a flat plate, still drawn", () => {
    expect(isoBar(0, 50, 10, 4, 0).front).toBe("0,50 10,50 10,50 0,50");
  });
});

const lead = (id: number, over: Partial<Lead> = {}): Lead =>
  ({ id, business: `b${id}`, status: "new", score: null, score_read_at: null,
     last_touch_at: null, created_at: null, outreach_state: null, ...over } as Lead);

describe("latestLeads", () => {
  it("takes at most six and keeps server order", () => {
    const r = latestLeads(Array.from({ length: 9 }, (_, i) => lead(i + 1)));
    expect(r.map((l) => l.id)).toEqual([1, 2, 3, 4, 5, 6]);
  });
  it("a missing score stays null; touch falls back to created, then null", () => {
    const [a, b, c] = latestLeads([
      lead(1, { score: 0, last_touch_at: "t1", created_at: "c1" }),
      lead(2, { created_at: "c2" }), lead(3),
    ]);
    expect(a).toMatchObject({ score: 0, at: "t1" });
    expect(b).toMatchObject({ score: null, at: "c2" });
    expect(c).toMatchObject({ at: null });
  });
});
