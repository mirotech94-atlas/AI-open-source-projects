import type { SVGAttributes } from "react";
import type { Tone } from "../lib/format";

export type SparklineProps = {
  /** Oldest first, e.g. `SiteCard.latency_spark`. */
  points: number[];
  height?: number;
  tone?: Tone;
  className?: string;
  // `points` is also an SVG presentation attribute (polyline/polygon), so it
  // has to be omitted or the two declarations intersect to `never`.
} & Omit<SVGAttributes<SVGSVGElement>, "children" | "height" | "viewBox" | "points">;

const VIEW_W = 100;

/**
 * A bare trend line: no axes, no ticks, no library. It stretches to its
 * container's width through a `viewBox` with `preserveAspectRatio="none"`, and
 * the stroke stays 1.5px because of `vector-effect: non-scaling-stroke`.
 */
export function Sparkline({
  points,
  height = 28,
  tone = "plain",
  className = "",
  ...rest
}: SparklineProps) {
  const clean = points.filter((p) => Number.isFinite(p));
  if (clean.length < 2) {
    // One reading is not a trend; render an empty box of the same size so the
    // surrounding layout does not jump when the second scan lands.
    return (
      <svg
        className={`g-spark ${className}`.trim()}
        height={height}
        viewBox={`0 0 ${VIEW_W} ${height}`}
        preserveAspectRatio="none"
        aria-hidden="true"
        {...rest}
      />
    );
  }

  const min = Math.min(...clean);
  const max = Math.max(...clean);
  const span = max - min || 1;
  const pad = 2;
  const usable = Math.max(1, height - pad * 2);
  const d = clean
    .map((v, i) => {
      const x = (i / (clean.length - 1)) * VIEW_W;
      const y = pad + (1 - (v - min) / span) * usable;
      return `${i === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");

  return (
    <svg
      className={`g-spark g-tone-${tone} ${className}`.trim()}
      height={height}
      viewBox={`0 0 ${VIEW_W} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`Trend, ${clean.length} points, ${min} to ${max}`}
      {...rest}
    >
      <path className="g-spark-path" d={d} />
    </svg>
  );
}

export default Sparkline;
