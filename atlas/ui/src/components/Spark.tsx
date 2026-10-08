import { useId, useState, type JSX, type PointerEvent } from "react";
import {
  formatValue, healthBarLine, layoutSeries, type HealthBar, type Mark, type Series,
  type SeriesPoint,
} from "../pages/liveModel";

/**
 * Small charts in plain SVG. They draw stored readings and nothing else:
 * straight segments between readings, a break where readings stop, and an
 * UNKNOWN reading as a grey hollow mark on its own track under the line --
 * never a point at zero (invariant 7). Hover, focus or tap shows the exact
 * value and its read time.
 */

const W = 240;
const H = 52;

function stamp(t: number): string {
  return new Date(t).toLocaleString([], {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    hour12: false,
  });
}

type Picked = { kind: "point"; p: SeriesPoint } | { kind: "mark"; m: Mark };

function pickedLine(x: Picked, unit: string | null): string {
  if (x.kind === "mark") {
    const what = x.m.kind === "unknown" ? "UNKNOWN: the check could not read" : "read, but no number";
    return `${what} · read ${stamp(x.m.t)}`;
  }
  const v = formatValue(x.p.v, unit);
  return `${v.text}${v.unit ? ` ${v.unit}` : ""} · read ${stamp(x.p.t)}`
    + (x.p.health !== "ok" ? ` · ${x.p.health}` : "");
}

export function ReadingsChart({
  series, from, to, label, note = null,
}: {
  series: Series;
  from: number;
  to: number;
  label: string;
  note?: string | null;
}): JSX.Element {
  const [picked, setPicked] = useState<Picked | null>(null);
  const geo = layoutSeries(series, from, to, W, H);
  const all: Array<{ x: number; pick: Picked }> = [
    ...geo.lines.flat().map((q) => ({ x: q.x, pick: { kind: "point", p: q.p } as Picked })),
    ...geo.marks.map((q) => ({ x: q.x, pick: { kind: "mark", m: q.m } as Picked })),
  ];
  const nearest = (clientX: number, el: SVGSVGElement): Picked | null => {
    const box = el.getBoundingClientRect();
    if (box.width === 0 || all.length === 0) return null;
    const x = ((clientX - box.left) / box.width) * W;
    let best: { d: number; pick: Picked } | null = null;
    for (const a of all) {
      const d = Math.abs(a.x - x);
      if (!best || d < best.d) best = { d, pick: a.pick };
    }
    return best && best.d <= 24 ? best.pick : null;
  };
  const onPointer = (e: PointerEvent<SVGSVGElement>): void =>
    setPicked(nearest(e.clientX, e.currentTarget));
  const last = geo.lines.flat().at(-1);
  const summary = series.readings === 0
    ? "no readings in these 7 days"
    : `${series.readings} reading${series.readings === 1 ? "" : "s"} in 7 days`
      + (series.marks.length ? `, ${series.marks.length} without a number` : "")
      + (series.otherUnit ? `, ${series.otherUnit} in an older unit left off` : "");

  return (
    <figure className="ac-spark" data-empty={series.readings === 0 || undefined}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label}: ${summary}`}
           onPointerMove={onPointer} onPointerDown={onPointer}
           onPointerLeave={() => setPicked(null)}>
        <line className="ac-spark-track" x1={0} x2={W} y1={H - 6} y2={H - 6} />
        {geo.lines.map((seg, i) => seg.length === 1 ? (
          <circle key={i} className="ac-spark-dot" cx={seg[0]!.x} cy={seg[0]!.y} r={2.6} />
        ) : (
          <polyline key={i} className="ac-spark-line"
                    points={seg.map((q) => `${q.x.toFixed(1)},${q.y.toFixed(1)}`).join(" ")} />
        ))}
        {last && <circle className="ac-spark-last" cx={last.x} cy={last.y} r={3} />}
        {geo.marks.map((q, i) => (
          <circle key={`m${i}`} className="ac-spark-mark" data-kind={q.m.kind}
                  cx={q.x} cy={q.y} r={3} />
        ))}
        {picked && (
          <line className="ac-spark-cursor" y1={0} y2={H}
                x1={picked.kind === "point" ? layoutX(picked.p.t, from, to) : layoutX(picked.m.t, from, to)}
                x2={picked.kind === "point" ? layoutX(picked.p.t, from, to) : layoutX(picked.m.t, from, to)} />
        )}
      </svg>
      <figcaption className="ac-spark-cap" aria-live="polite">
        {picked ? pickedLine(picked, series.unit) : (note ? `${summary} · ${note}` : summary)}
      </figcaption>
    </figure>
  );
}

function layoutX(t: number, from: number, to: number): number {
  return ((t - from) / Math.max(1, to - from)) * W;
}

const ORDER = ["failing", "degraded", "unknown", "ok"] as const;

/** Modules by state, hour by hour: stacked bars, UNKNOWN hatched grey. */
export function HealthBars({ bars, from, to }: {
  bars: HealthBar[];
  from: number;
  to: number;
}): JSX.Element {
  const [picked, setPicked] = useState<HealthBar | null>(null);
  const hatch = useId().replace(/:/g, "");
  const top = Math.max(1, ...bars.map((b) => b.total));
  const bw = bars.length > 1 ? Math.max(0.8, (W / bars.length) * 0.8) : 4;
  const onPointer = (e: PointerEvent<SVGSVGElement>): void => {
    const box = e.currentTarget.getBoundingClientRect();
    if (box.width === 0 || bars.length === 0) return;
    const t = from + ((e.clientX - box.left) / box.width) * (to - from);
    let best = bars[0] ?? null;
    for (const b of bars) if (best && Math.abs(b.t - t) < Math.abs(best.t - t)) best = b;
    setPicked(best);
  };
  return (
    <figure className="ac-spark ac-hbars">
      <svg viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label={`Modules by state, hourly over 7 days, ${bars.length} samples`}
           onPointerMove={onPointer} onPointerDown={onPointer}
           onPointerLeave={() => setPicked(null)}>
        <defs>
          <pattern id={hatch} width="4" height="4" patternUnits="userSpaceOnUse"
                   patternTransform="rotate(45)">
            <rect width="4" height="4" className="ac-hbar-unknown-bg" />
            <line x1="0" y1="0" x2="0" y2="4" className="ac-hbar-unknown-line" />
          </pattern>
        </defs>
        {bars.map((b) => {
          const x = layoutX(b.t, from, to) - bw / 2;
          let y = H;
          return (
            <g key={b.t} data-picked={picked?.t === b.t || undefined}>
              {ORDER.map((k) => {
                const n = b.counts[k];
                if (!n) return null;
                const h = (n / top) * (H - 2);
                y -= h;
                return (
                  <rect key={k} x={x} y={y} width={bw} height={h} data-health={k}
                        className="ac-hbar" fill={k === "unknown" ? `url(#${hatch})` : undefined} />
                );
              })}
            </g>
          );
        })}
      </svg>
      <figcaption className="ac-spark-cap" aria-live="polite">
        {picked ? `${stamp(picked.t)} · ${healthBarLine(picked)}`
          : bars.length ? "Hourly, last 7 days. Hover or tap a bar for its counts." : "No samples."}
      </figcaption>
    </figure>
  );
}
