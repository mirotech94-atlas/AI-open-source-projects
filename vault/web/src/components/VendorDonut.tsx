import { useId, useMemo, useState } from "react";
import type { DeviceCard } from "../types";

export type VendorDonutProps = {
  devices: DeviceCard[];
  /** Vendors drawn before the tail collapses into "Other". Five is the
   *  categorical palette's size; a sixth hue would have to be generated,
   *  and a generated hue is indistinguishable under colour-blindness. */
  top?: number;
};

const OTHER = "Other";
const UNKNOWN = "Not identified";

type Slice = { vendor: string; count: number; rest: boolean; unknown?: boolean };

/** Devices per vendor, largest first, tail collapsed.
 *  `Other` and `Not identified` always sort last however big they are:
 *  they are the buckets you read past, not findings. */
export function vendorCounts(devices: DeviceCard[], top: number): Slice[] {
  const counts = new Map<string, number>();
  for (const d of devices) {
    const vendor = d.vendor.trim() === "" ? UNKNOWN : d.vendor.trim();
    counts.set(vendor, (counts.get(vendor) ?? 0) + 1);
  }
  const named: Slice[] = [...counts.entries()]
    .filter(([vendor]) => vendor !== UNKNOWN)
    .map(([vendor, count]) => ({ vendor, count, rest: false }))
    .sort((a, b) => b.count - a.count || a.vendor.localeCompare(b.vendor));

  const shown = named.slice(0, top);
  const tail = named.slice(top).reduce((sum, r) => sum + r.count, 0);
  if (tail > 0) shown.push({ vendor: OTHER, count: tail, rest: true });
  const unknown = counts.get(UNKNOWN) ?? 0;
  if (unknown > 0) shown.push({ vendor: UNKNOWN, count: unknown, rest: true, unknown: true });
  return shown;
}

const R = 52;
const STROKE = 22;
const C = 2 * Math.PI * R;

/**
 * Who makes the devices on this network, as a share of the whole.
 *
 * A donut rather than the old bar "racers": the question is what this
 * network is MADE of, which is a part-to-whole reading, and a row of bars
 * racing rightwards answered "which vendor is biggest" — a question nobody
 * was asking.
 *
 * Colour never carries identity alone: every slice is also in the legend
 * with its own count and share, and the whole thing has a table underneath
 * for a screen reader. A 2px surface-coloured gap separates neighbouring
 * arcs so two slices never bleed into one shape.
 */
export function VendorDonut({ devices, top = 5 }: VendorDonutProps) {
  const rows = useMemo(() => vendorCounts(devices, top), [devices, top]);
  const [hover, setHover] = useState<string | null>(null);
  const titleId = useId();

  const total = rows.reduce((n, r) => n + r.count, 0);
  if (total === 0) return <p className="g-asset-empty">No devices have been seen yet.</p>;

  let offset = 0;
  const arcs = rows.map((row, i) => {
    const frac = row.count / total;
    const arc = { ...row, frac, dash: frac * C, offset, tone: row.unknown ? "var(--chart-unknown)" : row.rest ? "var(--chart-rest)" : `var(--chart-${(i % 5) + 1})` };
    offset += frac * C;
    return arc;
  });
  const lead = arcs.reduce((a, b) => (b.count > a.count ? b : a), arcs[0]);
  const shown = hover ? arcs.find((a) => a.vendor === hover) ?? lead : lead;
  const pct = (f: number) => `${Math.round(f * 100)}%`;

  return (
    <div className="g-vendor-donut" data-testid="vendor-donut">
      <div className="g-vendor-donut-fig">
        <svg viewBox="0 0 140 140" role="img" aria-labelledby={titleId}>
          <title id={titleId}>
            {`Devices by manufacturer: ${arcs.map((a) => `${a.vendor} ${a.count}`).join(", ")}`}
          </title>
          <g transform="translate(70,70) rotate(-90)">
            <circle r={R} fill="none" stroke="var(--color-track)" strokeWidth={STROKE} />
            {arcs.map((a) => (
              <circle
                key={a.vendor}
                r={R}
                fill="none"
                stroke={a.tone}
                strokeWidth={hover === a.vendor ? STROKE + 4 : STROKE}
                /* 2px of surface between neighbours so two arcs never read
                   as one shape; never wider than the arc itself. */
                strokeDasharray={`${Math.max(0, a.dash - 2)} ${C - Math.max(0, a.dash - 2)}`}
                strokeDashoffset={-a.offset}
                onMouseEnter={() => setHover(a.vendor)}
                onMouseLeave={() => setHover(null)}
              />
            ))}
          </g>
          {/* The centre reads the hovered slice, or the largest at rest —
              a donut with an empty hole makes the reader do the division. */}
          <text x="70" y="65" className="g-vendor-donut-n" textAnchor="middle">{shown.count}</text>
          <text x="70" y="80" className="g-vendor-donut-l" textAnchor="middle">{pct(shown.frac)}</text>
        </svg>
        <p className="g-vendor-donut-cap">
          <strong>{shown.vendor}</strong> · {shown.count} of {total} devices
        </p>
      </div>

      <ul className="g-vendor-legend">
        {arcs.map((a) => (
          <li
            key={a.vendor}
            className={`g-vendor-legend-row${hover === a.vendor ? " is-hover" : ""}`}
            onMouseEnter={() => setHover(a.vendor)}
            onMouseLeave={() => setHover(null)}
          >
            <span className="g-vendor-swatch" style={{ background: a.tone }} aria-hidden="true" />
            <span className="g-vendor-name">{a.vendor}</span>
            <span className="g-vendor-count num">{a.count}</span>
            <span className="g-vendor-pct num">{pct(a.frac)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default VendorDonut;
