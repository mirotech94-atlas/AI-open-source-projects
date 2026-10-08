// Crosshair sampling for `Chart`, kept apart from the component so it can be
// tested without a layout engine.
//
// The rule the crosshair has to keep: every series is read at ONE instant. If
// each series picked its own nearest point, two series whose timestamps are
// offset would report values hours apart under a single vertical line, which is
// exactly the kind of quiet lie a monitoring chart must not tell.

export type ChartPoint = { t: number; v: number | null };

export type HoverRow = {
  /** Index into the `series` array this row came from. */
  index: number;
  /** `null` when the series has no reading near the shared instant. */
  value: number | null;
};

export type HoverSample = {
  /** The shared instant, in epoch ms. */
  t: number;
  rows: HoverRow[];
};

function medianGap(points: ChartPoint[]): number {
  if (points.length < 2) return Number.POSITIVE_INFINITY;
  const gaps: number[] = [];
  for (let i = 1; i < points.length; i += 1) gaps.push(points[i].t - points[i - 1].t);
  gaps.sort((a, b) => a - b);
  return gaps[Math.floor(gaps.length / 2)];
}

/**
 * Picks the instant nearest the cursor from the union of every series'
 * timestamps, then reads each series at that instant. A series with no point
 * within half its own typical spacing reports `null` rather than a value
 * borrowed from far away.
 *
 * `series` is expected sorted by `t` ascending, which `Chart` guarantees.
 */
export function sampleAt(series: ChartPoint[][], cursorT: number): HoverSample | null {
  const all = series.flat();
  if (all.length === 0) return null;

  let t = all[0].t;
  for (const p of all) {
    if (Math.abs(p.t - cursorT) < Math.abs(t - cursorT)) t = p.t;
  }

  const rows: HoverRow[] = series.map((points, index) => {
    if (points.length === 0) return { index, value: null };
    let best = points[0];
    for (const p of points) {
      if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    }
    const gap = medianGap(points);
    const tolerance = gap === Number.POSITIVE_INFINITY ? Number.POSITIVE_INFINITY : gap / 2;
    if (Math.abs(best.t - t) > tolerance) return { index, value: null };
    return { index, value: best.v };
  });

  return { t, rows };
}
