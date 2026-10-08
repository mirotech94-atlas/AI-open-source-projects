import type { JSX } from "react";

/**
 * A run count per bucket, drawn small.
 *
 * Deliberately not a chart: no axis, no grid, no tooltip. It answers one
 * question — did this agent do anything, and when — and the exact figures live
 * in the title, which carries the window it was read over.
 *
 * An empty series draws a flat dashed rule in grey rather than a line along
 * the bottom. A line at zero says "it ran nothing"; a dash says "there is
 * nothing here to draw", and those are different claims.
 */
export function Sparkline({
  values,
  label,
  width = 96,
  height = 20,
}: {
  values: number[];
  label: string;
  width?: number;
  height?: number;
}): JSX.Element {
  const peak = values.reduce((m, v) => Math.max(m, v), 0);
  const drawable = values.length >= 2 && peak > 0;

  if (!drawable) {
    return (
      <svg className="spark" data-empty="true" width={width} height={height}
           viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${label}: nothing`}>
        <title>{label}: nothing in this window</title>
        <line x1="0" y1={height - 2} x2={width} y2={height - 2}
              stroke="var(--grey)" strokeWidth="1" strokeDasharray="3 4" />
      </svg>
    );
  }

  const step = width / (values.length - 1);
  const points = values
    .map((v, i) => `${(i * step).toFixed(1)},${(height - 2 - (v / peak) * (height - 4)).toFixed(1)}`)
    .join(" ");

  return (
    <svg className="spark" data-empty="false" width={width} height={height}
         viewBox={`0 0 ${width} ${height}`} role="img"
         aria-label={`${label}: peak ${peak}`}>
      <title>{`${label} — peak ${peak} in one bucket`}</title>
      <polyline points={points} fill="none" stroke="var(--cyan)"
                strokeWidth="1.2" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
