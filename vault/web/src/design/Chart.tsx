import { useEffect, useMemo, useRef, useState } from "react";
import type { HTMLAttributes } from "react";
import { quantile } from "d3-array";
import { scaleLinear, scaleUtc } from "d3-scale";
import { line } from "d3-shape";
import type { Series } from "../types";
import { clock, day, num } from "../lib/format";
import { sampleAt } from "./chartHover";
import type { ChartPoint } from "./chartHover";

export type ChartProps = {
  /** At most two series are drawn: the third onward is dropped, because two is
   *  the most a single-accent palette can distinguish honestly. */
  series: Series[];
  height?: number;
  yLabel?: string;
  /** Pins the y domain's top. Without it the domain is derived, with outliers
   *  clipped at the 99th percentile and a note saying so. */
  yMax?: number;
  format?: (value: number) => string;
  /** Enables drag-to-select a time range. */
  brush?: boolean;
  onBrush?: (range: [string, string]) => void;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children">;

// The only two blues a chart may use, in this order.
const SERIES_COLORS = ["var(--color-primary)", "var(--color-accent-2)"] as const;
const MARGIN = { top: 10, right: 12, bottom: 22, left: 44 };
const MIN_BRUSH_PX = 6;

export function Chart({
  series,
  height = 200,
  yLabel,
  yMax,
  format = num,
  brush = false,
  onBrush,
  className = "",
  ...rest
}: ChartProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(640);
  const [hoverX, setHoverX] = useState<number | null>(null);
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);

  // Measure the container instead of guessing: the same chart sits in a wide
  // Overview card and in a narrow drawer.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const drawn = useMemo(() => series.slice(0, 2), [series]);

  const parsed = useMemo<ChartPoint[][]>(
    () =>
      drawn.map((s) =>
        s.points
          .map((p) => ({ t: new Date(p.ts).getTime(), v: p.value }))
          .filter((p) => Number.isFinite(p.t))
          .sort((a, b) => a.t - b.t),
      ),
    [drawn],
  );

  const values = useMemo(
    () =>
      parsed
        .flat()
        .map((p) => p.v)
        .filter((v): v is number => v !== null && Number.isFinite(v)),
    [parsed],
  );
  const times = useMemo(() => parsed.flat().map((p) => p.t), [parsed]);

  const innerW = Math.max(1, width - MARGIN.left - MARGIN.right);
  const innerH = Math.max(1, height - MARGIN.top - MARGIN.bottom);

  const hasData = times.length > 0 && values.length > 0;

  // Percentile clipping: one pathological spike must not flatten the rest of
  // the series into a straight line. When the maximum is far above the 99th
  // percentile the domain stops at p99 and the chart says so in a footnote.
  const { top, clipped } = useMemo(() => {
    if (yMax !== undefined) return { top: yMax, clipped: false };
    if (values.length === 0) return { top: 1, clipped: false };
    const max = Math.max(...values);
    const p99 = quantile([...values].sort((a, b) => a - b), 0.99) ?? max;
    if (p99 > 0 && max > p99 * 1.5) return { top: p99 * 1.05, clipped: true };
    return { top: max === 0 ? 1 : max * 1.05, clipped: false };
  }, [values, yMax]);

  const x = useMemo(() => {
    const lo = times.length ? Math.min(...times) : 0;
    const hi = times.length ? Math.max(...times) : 1;
    return scaleUtc()
      .domain([new Date(lo), new Date(hi === lo ? lo + 1 : hi)])
      .range([0, innerW]);
  }, [times, innerW]);

  const y = useMemo(
    () => scaleLinear().domain([0, top]).range([innerH, 0]).nice(),
    [top, innerH],
  );

  const lineGen = useMemo(
    () =>
      line<ChartPoint>()
        .defined((p) => p.v !== null && Number.isFinite(p.v))
        // Clipping the *domain* would drop points; clamping the drawn value
        // keeps the shape and pins the spike to the top edge instead.
        .x((p) => x(new Date(p.t)))
        .y((p) => y(Math.min(p.v as number, top))),
    [x, y, top],
  );

  const ticks = useMemo(() => x.ticks(Math.max(2, Math.floor(innerW / 90))), [x, innerW]);
  const yTicks = useMemo(() => y.ticks(4), [y]);

  // Every series is read at ONE shared instant, so the crosshair never lines up
  // two values that were measured hours apart. See `chartHover.ts`.
  const hover = useMemo(() => {
    if (hoverX === null || !hasData) return null;
    const sample = sampleAt(parsed, x.invert(hoverX).getTime());
    if (sample === null) return null;
    const px = x(new Date(sample.t));
    const rows = sample.rows.map((r) => ({
      name: drawn[r.index].name,
      value: r.value,
      color: SERIES_COLORS[r.index],
      cy: r.value === null ? innerH : y(Math.min(r.value, top)),
    }));
    return { ts: sample.t, rows, px };
  }, [hoverX, hasData, x, y, parsed, drawn, innerH, top]);

  function localX(e: React.MouseEvent<SVGSVGElement>): number {
    const box = e.currentTarget.getBoundingClientRect();
    return Math.min(innerW, Math.max(0, e.clientX - box.left - MARGIN.left));
  }

  function endBrush() {
    if (!drag) return;
    const [a, b] = [Math.min(drag.from, drag.to), Math.max(drag.from, drag.to)];
    setDrag(null);
    if (!onBrush || b - a < MIN_BRUSH_PX) return;
    onBrush([x.invert(a).toISOString(), x.invert(b).toISOString()]);
  }

  const label = hasData
    ? `${yLabel ?? "Trend"}: ${drawn.map((s) => s.name).join(", ")}`
    : `${yLabel ?? "Trend"}: no data yet`;

  return (
    <div ref={wrapRef} className={`g-chart ${className}`.trim()} {...rest}>
      {!hasData ? (
        <p className="g-chart-empty">No data for this range yet.</p>
      ) : (
        <>
          <svg
            className="g-chart-svg"
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-label={label}
            onMouseMove={(e) => {
              const px = localX(e);
              setHoverX(px);
              if (drag) setDrag({ from: drag.from, to: px });
            }}
            onMouseLeave={() => {
              setHoverX(null);
              setDrag(null);
            }}
            onMouseDown={(e) => {
              if (!brush) return;
              const px = localX(e);
              setDrag({ from: px, to: px });
            }}
            onMouseUp={endBrush}
          >
            <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
              <g className="g-chart-grid">
                {yTicks.map((t) => (
                  <line key={t} x1={0} x2={innerW} y1={y(t)} y2={y(t)} />
                ))}
              </g>
              <g className="g-chart-axis">
                {yTicks.map((t) => (
                  <text key={t} x={-8} y={y(t)} dy="0.32em" textAnchor="end">
                    {format(t)}
                  </text>
                ))}
                {ticks.map((t) => (
                  <text
                    key={t.getTime()}
                    x={x(t)}
                    y={innerH + 16}
                    textAnchor="middle"
                  >
                    {innerW > 0 && x.domain()[1].getTime() - x.domain()[0].getTime() > 86_400_000
                      ? day(t.toISOString())
                      : clock(t.toISOString())}
                  </text>
                ))}
              </g>
              {yLabel !== undefined && (
                <text
                  className="g-chart-ylabel"
                  transform={`translate(${-MARGIN.left + 10},${innerH / 2}) rotate(-90)`}
                  textAnchor="middle"
                >
                  {yLabel}
                </text>
              )}
              {drag && (
                <rect
                  className="g-chart-brush"
                  x={Math.min(drag.from, drag.to)}
                  width={Math.abs(drag.to - drag.from)}
                  y={0}
                  height={innerH}
                />
              )}
              {parsed.map((pts, i) => (
                <path
                  key={drawn[i].name}
                  className="g-chart-line"
                  stroke={SERIES_COLORS[i]}
                  d={lineGen(pts) ?? undefined}
                />
              ))}
              {hover && (
                <>
                  <line
                    className="g-chart-crosshair"
                    x1={hover.px}
                    x2={hover.px}
                    y1={0}
                    y2={innerH}
                  />
                  {hover.rows.map((r) =>
                    r.value === null ? null : (
                      <circle key={r.name} cx={hover.px} cy={r.cy} r={3} fill={r.color} />
                    ),
                  )}
                </>
              )}
            </g>
          </svg>
          {hover && (
            <div
              className="g-chart-tip"
              style={
                hover.px + MARGIN.left > width / 2
                  ? { right: width - hover.px - MARGIN.left + 8 }
                  : { left: hover.px + MARGIN.left + 8 }
              }
            >
              <div className="g-chart-tip-ts">{clock(new Date(hover.ts).toISOString())}</div>
              {hover.rows.map((r) => (
                <div className="g-chart-tip-row" key={r.name}>
                  <span className="g-chart-tip-swatch" style={{ background: r.color }} />
                  <span>{r.name}</span>
                  <span className="g-chart-tip-value">
                    {r.value === null ? "—" : format(r.value)}
                  </span>
                </div>
              ))}
            </div>
          )}
          {clipped && (
            <p className="g-chart-note">
              Clipped at the 99th percentile — a few readings run higher.
            </p>
          )}
          {brush && (
            <p className="g-chart-note">Drag across the chart to zoom to a range.</p>
          )}
        </>
      )}
    </div>
  );
}

export default Chart;
