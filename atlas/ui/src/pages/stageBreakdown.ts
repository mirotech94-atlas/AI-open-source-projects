/**
 * Pipeline stage counts for the stage bar. Same rule as CommandFloorBody:
 * unless both the sites and the leads read are "ok" there is no breakdown
 * (null), so the panel shows UNKNOWN and never a zero.
 */
import type { PipelineRead } from "../api";

export interface StageCount {
  stage: string;
  count: number;
}

export interface StageBreakdown {
  stages: StageCount[];
  total: number;
  readAt: string;
}

export function stageBreakdown(
  pipe: PipelineRead | null,
  error: string | null,
): StageBreakdown | null {
  if (pipe === null || error !== null) return null;
  if (pipe.sites.state !== "ok" || pipe.leads.state !== "ok") return null;
  const counts = new Map<string, number>();
  for (const r of pipe.rows) {
    const key = r.stage && r.stage.trim() ? r.stage : "no stage";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return {
    stages: [...counts].map(([stage, count]) => ({ stage, count })),
    total: pipe.rows.length,
    readAt: pipe.read_at,
  };
}
