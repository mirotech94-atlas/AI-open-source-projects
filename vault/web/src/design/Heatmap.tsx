import { useMemo, useState } from "react";
import type { HTMLAttributes } from "react";
import type { HeatmapCell } from "../types";

export type HeatmapProps = {
  /** Already normalised 0–100 by the hub. This component never aggregates. */
  cells: HeatmapCell[];
  /** Row labels, top to bottom — e.g. one device or host per row. */
  rows: string[];
  /** Column values, left to right — e.g. hours 0–23 or day offsets. */
  cols: number[];
  /** Formats the hover readout. Defaults to the raw 0–100 value. */
  format?: (value: number) => string;
  onCell?: (cell: HeatmapCell) => void;
  /** Row labels are ids (a MAC, say); this maps one to something readable. */
  rowLabel?: (row: string) => string;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children">;

/**
 * A labelled grid ramped in a single accent, from `--color-track` to
 * `--color-primary`. One accent, one meaning — never a rainbow scale.
 */
export function Heatmap({
  cells,
  rows,
  cols,
  format = (v) => String(Math.round(v)),
  onCell,
  rowLabel = (r) => r,
  className = "",
  ...rest
}: HeatmapProps) {
  const [hover, setHover] = useState<HeatmapCell | null>(null);

  const byKey = useMemo(() => {
    const m = new Map<string, HeatmapCell>();
    for (const c of cells) m.set(`${c.col}|${c.row}`, c);
    return m;
  }, [cells]);

  if (rows.length === 0 || cols.length === 0) {
    return (
      <div className={`g-heat ${className}`.trim()} {...rest}>
        <p className="g-chart-empty">Nothing recorded for this range yet.</p>
      </div>
    );
  }

  return (
    <div className={`g-heat ${className}`.trim()} {...rest}>
      <table className="g-heat-grid">
        <thead>
          <tr>
            <th />
            {cols.map((c) => (
              <th key={c} scope="col">
                {/* Every other column label, so 24 hours stay readable. */}
                {c % 2 === 0 ? c : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r}>
              <th scope="row" title={r}>
                {rowLabel(r)}
              </th>
              {cols.map((c) => {
                const cell = byKey.get(`${c}|${r}`) ?? { row: r, col: c, value: 0 };
                const mix = Math.min(100, Math.max(0, cell.value));
                const readout = `${rowLabel(r)}, ${c}: ${format(cell.value)}`;
                return (
                  <td key={c}>
                    <button
                      type="button"
                      className="g-heat-cell"
                      data-testid="heat-cell"
                      style={{
                        background: `color-mix(in srgb, var(--color-primary) ${mix}%, var(--color-track))`,
                      }}
                      title={readout}
                      aria-label={readout}
                      onMouseEnter={() => setHover(cell)}
                      onFocus={() => setHover(cell)}
                      onMouseLeave={() => setHover(null)}
                      onBlur={() => setHover(null)}
                      onClick={onCell ? () => onCell(cell) : undefined}
                    />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="g-heat-legend">
        <span>less</span>
        <span className="g-heat-legend-ramp" aria-hidden="true" />
        <span>more</span>
        <span className="g-heat-readout" role="status">
          {hover === null ? "" : `${rowLabel(hover.row)} · ${hover.col} · ${format(hover.value)}`}
        </span>
      </div>
    </div>
  );
}

export default Heatmap;
