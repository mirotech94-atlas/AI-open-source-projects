import type { HTMLAttributes, ReactNode } from "react";
import { ringTone } from "../lib/format";

export type RingProps = {
  value: number | null;
  max?: number;
  /** Letter grade drawn under the number, e.g. `OverviewPayload.grade`. */
  grade?: ReactNode;
  size?: number;
  /** Accessible name, e.g. "Security score". Required — a bare meter is mute. */
  label: string;
  /** The reference console's own treatment: a figure is a cell in the grid,
   *  not a banner — no arc, no track, just the number and the grade, still
   *  exposed as the same `role="meter"` so it reads identically to a screen
   *  reader. Used for the one score that used to anchor the whole page. */
  flat?: boolean;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children">;

export function Ring({
  value,
  max = 100,
  grade,
  size = 120,
  label,
  flat = false,
  className = "",
  ...rest
}: RingProps) {
  const tone = ringTone(value, max);

  if (flat) {
    return (
      <div className={`g-ring g-ring-flat g-tone-${tone} ${className}`.trim()} {...rest}>
        <span
          className="g-ring-flat-value mono"
          role="meter"
          aria-label={label}
          aria-valuenow={value === null ? undefined : value}
          aria-valuemin={0}
          aria-valuemax={max}
          aria-valuetext={value === null ? "no score yet" : undefined}
        >
          {value === null ? "--" : Math.round(value)}
        </span>
        {grade !== undefined && <span className="g-ring-flat-grade">{grade}</span>}
      </div>
    );
  }

  const stroke = Math.max(6, Math.round(size / 14));
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const frac = value === null || max <= 0 ? 0 : Math.min(1, Math.max(0, value / max));
  const offset = circumference * (1 - frac);

  return (
    <div className={`g-ring g-tone-${tone} ${className}`.trim()} {...rest}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        // The meter role, its name and all three values live on one element, so
        // a screen reader announces "Security score, 83".
        role="meter"
        aria-label={label}
        aria-valuenow={value === null ? undefined : value}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuetext={value === null ? "no score yet" : undefined}
      >
        <circle
          className="g-ring-track"
          cx={size / 2}
          cy={size / 2}
          r={r}
          strokeWidth={stroke}
        />
        <circle
          className="g-ring-arc"
          cx={size / 2}
          cy={size / 2}
          r={r}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
        <text
          className="g-ring-value"
          x={size / 2}
          y={grade === undefined ? size / 2 + size * 0.1 : size / 2 + size * 0.04}
          textAnchor="middle"
          fontSize={size * 0.3}
          aria-hidden="true"
        >
          {value === null ? "--" : Math.round(value)}
        </text>
        {grade !== undefined && (
          <text
            className="g-ring-grade"
            x={size / 2}
            y={size / 2 + size * 0.26}
            textAnchor="middle"
            fontSize={size * 0.14}
            aria-hidden="true"
          >
            {grade}
          </text>
        )}
      </svg>
    </div>
  );
}

export default Ring;
