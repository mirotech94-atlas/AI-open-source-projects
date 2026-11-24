import type {
  ExperimentStage, ExperimentState, Marketing, MarketingExperiment, MarketingPlanItem,
  MarketingSection,
} from "../api";

/** The kanban, left to right: what the loop drafted, what waits on Miro's
 *  word, what is public and being measured, and what has closed. */
export const LANES: ReadonlyArray<{ stage: ExperimentStage; title: string }> = [
  { stage: "draft", title: "Drafts" },
  { stage: "awaiting", title: "Awaiting approval" },
  { stage: "published", title: "Published" },
  { stage: "measured", title: "Measured" },
];

export function lanes(
  experiments: readonly MarketingExperiment[],
): Record<ExperimentStage, MarketingExperiment[]> {
  const out: Record<ExperimentStage, MarketingExperiment[]> = {
    draft: [], awaiting: [], published: [], measured: [],
  };
  for (const e of experiments) out[e.stage].push(e);
  return out;
}

/** The word on every card. Colour never carries the meaning on its own. */
export const STATE_WORD: Record<ExperimentState, string> = {
  draft: "Draft",
  awaiting: "Awaiting approval",
  measuring: "Measuring",
  overdue: "Overdue",
  won: "Won",
  lost: "Lost",
  flat: "Flat",
  unmeasured: "Unmeasured",
};

export type PanelView =
  | { kind: "reading" }
  | { kind: "unknown"; reason: string }
  | { kind: "rows" };

/**
 * What a panel may draw. The rule this exists for: an unreadable
 * `/api/marketing`, or a section the server could not read, is UNKNOWN —
 * never an empty list, because "no experiments" is a claim about the world
 * and "could not read" is a claim about the reading.
 */
export function panelView(
  data: Marketing | null,
  error: string | null,
  section: MarketingSection,
): PanelView {
  if (data) {
    const why = data.unavailable[section];
    return why ? { kind: "unknown", reason: why } : { kind: "rows" };
  }
  if (error) return { kind: "unknown", reason: `could not read /api/marketing: ${error}` };
  return { kind: "reading" };
}

/** "+64%", "−30%", "±0%" — or nothing, when there was no comparison. */
export function deltaText(pct: number | null): string | null {
  if (pct === null || !Number.isFinite(pct)) return null;
  const r = Math.round(pct);
  if (r > 0) return `+${r}%`;
  if (r < 0) return `−${Math.abs(r)}%`;
  return "±0%";
}

/** A figure's time, short enough to sit beside it. Null stays visible. */
export function when(iso: string | null): string {
  if (!iso) return "time not recorded";
  return new Date(iso).toLocaleString([], {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

export function num(value: number | null | undefined): string {
  return value === null || value === undefined
    ? "—"
    : value.toLocaleString([], { maximumFractionDigits: 0 });
}

/**
 * The plan panel's heading and rows. A proposal past its expiry can no longer
 * run — silence expired it — so it is never listed as waiting on him; it is
 * counted apart.
 */
export function planSummary(plan: readonly MarketingPlanItem[]): {
  live: MarketingPlanItem[];
  expired: MarketingPlanItem[];
  heading: string;
} {
  const live = plan.filter((p) => !p.expired);
  const expired = plan.filter((p) => p.expired);
  const heading = plan.length === 0
    ? "No publish decisions waiting on you."
    : `${live.length === 0 ? "Nothing" : live.length} waiting on you`
      + (expired.length ? `; ${expired.length} expired.` : ".");
  return { live, expired, heading };
}
