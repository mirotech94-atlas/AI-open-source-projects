/**
 * Pure shaping for the Command Center's 3D charts and lead grid (Miro
 * 2026-10-30). No fetching, so every rule is tested (chartModel.test.ts).
 * Invariants 6/7: a source that was not read is "unknown" (never a 0 bar);
 * a real empty result is a real zero with its read time.
 */
import type { Lead, PipelineRead } from "../api";
import type { RosterAgent } from "./opsFloorModel";

export type Unknown = { state: "unknown"; reason: string };

const MAX_STAGES = 6;

export interface StageBar { stage: string; count: number }
export type PipelineBars =
  | { state: "ok"; bars: StageBar[]; total: number; unstaged: number; readAt: string }
  | Unknown;

/** The pipeline read as a count per stage. Either source block unknown makes
 *  the whole chart unknown: a partial count would be a guessed number. */
export function pipelineBars(p: PipelineRead | null): PipelineBars {
  if (!p) return { state: "unknown", reason: "the pipeline was not read" };
  for (const b of [p.sites, p.leads]) {
    if (b.state !== "ok") return { state: "unknown", reason: b.reason ?? "a source was not read" };
  }
  const counts = new Map<string, number>();
  let unstaged = 0;
  for (const r of p.rows) {
    const s = r.stage?.trim().toLowerCase();
    if (!s) unstaged += 1;
    else counts.set(s, (counts.get(s) ?? 0) + 1);
  }
  const sorted = [...counts].map(([stage, count]) => ({ stage, count }))
    .sort((a, b) => b.count - a.count || a.stage.localeCompare(b.stage));
  const bars = sorted.length > MAX_STAGES
    ? [...sorted.slice(0, MAX_STAGES - 1), {
      stage: "other", count: sorted.slice(MAX_STAGES - 1).reduce((n, b) => n + b.count, 0),
    }] : sorted;
  return { state: "ok", bars, total: p.rows.length, unstaged, readAt: p.read_at };
}

export interface ShiftBar { name: string; count: number | null }
export type ShiftBars =
  | { state: "ok"; bars: ShiftBar[]; readAt: string | null }
  | Unknown;

/** Shifts today per agent (the roster's own `attempts_today`). A count that
 *  was not read stays null: an unknown bar, sorted last. */
export function shiftBars(agents: readonly RosterAgent[] | null): ShiftBars {
  if (!agents) return { state: "unknown", reason: "the roster was not read" };
  const bars = agents.map((a) => ({ name: a.name, count: a.attempts_today }))
    .sort((a, b) => (b.count ?? -1) - (a.count ?? -1) || a.name.localeCompare(b.name));
  return { state: "ok", bars, readAt: agents[0]?.read_at ?? null };
}

/** Pixel heights scaled to the tallest bar; null stays null. A non-zero
 *  count is never thinner than a 2 px sliver. */
export function scaleHeights(values: readonly (number | null)[], maxPx: number): (number | null)[] {
  const top = Math.max(0, ...values.map((v) => v ?? 0));
  return values.map((v) => (v === null ? null
    : top === 0 || v === 0 ? 0 : Math.max(2, Math.round((v / top) * maxPx))));
}

export interface IsoBar { front: string; top: string; side: string }

/** An extruded bar in oblique/isometric projection: a front face, a top face
 *  and a right face. (x, baseY) is the front-left foot, w wide, d deep
 *  (drawn as a d px right and d/2 px up shift), h tall. */
export function isoBar(x: number, baseY: number, w: number, d: number, h: number): IsoBar {
  const y = baseY - h;
  const dx = d, dy = d / 2;
  const P = (...p: number[][]): string => p.map(([a, b]) => `${a},${b}`).join(" ");
  return {
    front: P([x, baseY], [x + w, baseY], [x + w, y], [x, y]),
    top: P([x, y], [x + w, y], [x + w + dx, y - dy], [x + dx, y - dy]),
    side: P([x + w, baseY], [x + w + dx, baseY - dy], [x + w + dx, y - dy], [x + w, y]),
  };
}

export interface LeadRow {
  id: number; business: string; status: string; score: number | null;
  outreach: string | null; at: string | null;
}

/** The latest six leads, in the server's order (most recently touched first). */
export function latestLeads(leads: readonly Lead[]): LeadRow[] {
  return leads.slice(0, 6).map((l) => ({
    id: l.id, business: l.business, status: l.status, score: l.score ?? null,
    outreach: l.outreach_state ?? null, at: l.last_touch_at ?? l.created_at ?? null,
  }));
}
