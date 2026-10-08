import { useMemo, useState } from "react";
import { Card, Chart, Chips } from "../design";
import { num } from "../lib/format";
import type { HistoryChart } from "../types";

/**
 * One trend panel.
 *
 * `Chart` draws at most two series — two is the most a single-accent palette
 * separates honestly — so a panel with more entities than that (latency across
 * five sites, disk across four hosts) renders chips to choose which two are
 * drawn, defaulting to the first two. The data is all there; the reader picks
 * the comparison rather than the panel silently dropping the rest.
 *
 * Lifted out of `pages/History.tsx` unchanged so the Websites and Devices
 * trend panels are the same component reading the same hub-built
 * `HistoryChart` — three tabs cannot drift in how they draw a series.
 */
export function TrendPanel({ chart }: { chart: HistoryChart }) {
  const names = chart.series.map((s) => s.name);
  const [picked, setPicked] = useState<string[]>(() => names.slice(0, 2));

  const drawn = useMemo(() => {
    if (chart.series.length <= 2) return chart.series;
    const chosen = chart.series.filter((s) => picked.includes(s.name));
    return chosen.length > 0 ? chosen.slice(0, 2) : chart.series.slice(0, 2);
  }, [chart.series, picked]);

  return (
    <Card
      className="g-history-panel"
      title={chart.label}
      subtitle={chart.unit === "" ? undefined : chart.unit}
      actions={
        chart.series.length > 2 ? (
          <Chips
            multiple
            label={`Series shown in ${chart.label}`}
            items={names.map((n) => ({ value: n, label: n }))}
            value={picked}
            onChange={(next) => setPicked(next.slice(-2))}
          />
        ) : undefined
      }
    >
      <Chart
        series={drawn}
        height={150}
        yMax={chart.max ?? undefined}
        yLabel={chart.unit === "" ? undefined : chart.unit}
        format={(v) => (chart.unit === "" ? num(v) : `${num(v)}${chart.unit === "%" ? "%" : ` ${chart.unit}`}`)}
      />
    </Card>
  );
}

/** A row of trend panels, in the hub's own order. Renders nothing at all when
 *  the hub sent no chart — an empty panel is a claim about a metric we have no
 *  reading for. */
export function TrendPanels({ charts, label }: { charts: HistoryChart[]; label?: string }) {
  if (charts.length === 0) return null;
  return (
    <>
      {label !== undefined && <h2 className="g-section-label">{label}</h2>}
      <div className="g-history-grid">
        {charts.map((chart) => (
          <TrendPanel key={chart.key} chart={chart} />
        ))}
      </div>
    </>
  );
}

export default TrendPanels;
