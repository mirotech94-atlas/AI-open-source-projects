import { describe, expect, it } from "vitest";
import type { PipelineRead, PipelineRow } from "../api";
import { stageBreakdown } from "./stageBreakdown";

const row = (client: string, stage: string | null): PipelineRow => ({
  client, kind: "lead", stage, next_step: null, reason: null, read_at: null,
});
const pipe = (rows: PipelineRow[], sites: "ok" | "unknown" = "ok",
              leads: "ok" | "unknown" = "ok"): PipelineRead => ({
  as_of: "2026-11-23T10:00:00Z", rows, read_at: "2026-11-23T10:00:01Z",
  sites: { state: sites, read_at: null, reason: null },
  leads: { state: leads, read_at: null, reason: null },
});

describe("stageBreakdown", () => {
  it("counts rows per stage with the total and read time", () => {
    const r = stageBreakdown(pipe([row("a", "new"), row("b", "new"), row("c", "won")]), null);
    expect(r).not.toBeNull();
    expect(r!.stages).toEqual([{ stage: "new", count: 2 }, { stage: "won", count: 1 }]);
    expect(r!.total).toBe(3);
    expect(r!.readAt).toBe("2026-11-23T10:00:01Z");
  });
  it("a missing stage is its own bucket, not dropped", () => {
    const r = stageBreakdown(pipe([row("a", null)]), null);
    expect(r!.stages).toEqual([{ stage: "no stage", count: 1 }]);
  });
  it("null when nothing was read or the read failed", () => {
    expect(stageBreakdown(null, null)).toBeNull();
    expect(stageBreakdown(pipe([row("a", "new")]), "boom")).toBeNull();
  });
  it("null when sites or leads is not ok, never zero", () => {
    expect(stageBreakdown(pipe([], "unknown", "ok"), null)).toBeNull();
    expect(stageBreakdown(pipe([], "ok", "unknown"), null)).toBeNull();
  });
  it("an ok read of an empty pipeline is a real zero with a read time", () => {
    const r = stageBreakdown(pipe([]), null);
    expect(r).toEqual({ stages: [], total: 0, readAt: "2026-11-23T10:00:01Z" });
  });
});
