import { useMemo } from "react";
import type { HTMLAttributes } from "react";
import { Chips, Heatmap, Sparkline, Table } from "../design";
import type { Column } from "../design";
import { hours, pct, plural } from "../lib/format";
import type { ScreenTimePayload, ScreenTimeRow } from "../types";

export type ScreenTimeProps = {
  payload: ScreenTimePayload;
  days: number;
  onDaysChange: (days: number) => void;
  onOpenDevice: (mac: string) => void;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children">;

const DAY_OPTIONS = [
  { value: "1", label: "24h" },
  { value: "7", label: "7d" },
  // The reference console's middle range — a fortnight is what shows a
  // weekday/weekend pattern twice over.
  { value: "14", label: "14d" },
  { value: "30", label: "30d" },
];

// No IP, no trust column here on purpose — those are the Devices tab's
// facts, about identifying and vetting a thing on the network. This table
// answers a different, gentler question (how has the household actually
// spent its time), so it reads as that and nothing adjacent to it.
const COLUMNS: Column<ScreenTimeRow>[] = [
  { key: "name", header: "Device" },
  { key: "total_h", header: "Total", align: "right", render: (row) => hours(row.total_h) },
  { key: "pct", header: "Share", align: "right", render: (row) => pct(row.pct) },
  {
    key: "hours_per_day",
    header: "Per day",
    align: "right",
    render: (row) => hours(row.hours_per_day),
  },
  {
    key: "hours",
    header: "Trend",
    render: (row) => <Sparkline points={row.hours} />,
  },
];

/** Unique row/col axes straight from the hub's cells, in first-seen order —
 *  this never invents an hour or a device the hub did not report. */
function axesFrom(cells: ScreenTimePayload["heatmap"]): { rows: string[]; cols: number[] } {
  const rows: string[] = [];
  const cols: number[] = [];
  for (const cell of cells) {
    if (!rows.includes(cell.row)) rows.push(cell.row);
    if (!cols.includes(cell.col)) cols.push(cell.col);
  }
  cols.sort((a, b) => a - b);
  return { rows, cols };
}

export function ScreenTime({ payload, days, onDaysChange, onOpenDevice, className = "", ...rest }: ScreenTimeProps) {
  const { rows, cols } = useMemo(() => axesFrom(payload.heatmap), [payload.heatmap]);
  const nameByRow = useMemo(() => {
    const map = new Map<string, string>();
    for (const device of payload.devices) map.set(device.mac, device.name || device.mac);
    return map;
  }, [payload.devices]);

  return (
    <div className={`g-screen-time ${className}`.trim()} {...rest}>
      <p className="g-screen-time-lead">
        How much time each device on your network actually spent connected, by day.
      </p>
      <div className="g-screen-time-toolbar">
        <Chips
          label="Day range"
          items={DAY_OPTIONS}
          value={String(days)}
          onChange={(v) => onDaysChange(Number(v ?? "7"))}
        />
        <span className="hint">{plural(payload.scans, "scan")} over {plural(payload.days, "day")}</span>
      </div>

      <Table
        columns={COLUMNS}
        rows={payload.devices}
        rowKey={(row) => row.mac}
        onRowClick={(row) => onOpenDevice(row.mac)}
        empty="No screen time recorded yet. It fills in once the sensor has seen a full day."
      />

      <Heatmap
        cells={payload.heatmap}
        rows={rows}
        cols={cols}
        rowLabel={(row) => nameByRow.get(row) ?? row}
        format={(v) => pct(v)}
      />
    </div>
  );
}

export default ScreenTime;
