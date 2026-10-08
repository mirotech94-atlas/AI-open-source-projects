import { useState } from "react";
import { Badge, Table } from "../design";
import type { Column, SortState } from "../design";
import { DeviceIcon } from "./DeviceIcon";
import { NetworkBadge } from "./NetworkBadge";
import { ago } from "../lib/format";
import type { DeviceCard } from "../types";

function stateBadge(device: DeviceCard): { label: string; tone: "good" | "warn" | "alert" | "plain" } {
  if (device.quarantined) return { label: "Quarantined", tone: "alert" };
  if (device.unverified) return { label: "Unverified", tone: "warn" };
  if (device.online) return { label: "Online", tone: "good" };
  if (device.presence === "SLEEPING") return { label: "Sleeping", tone: "plain" };
  return { label: "Offline", tone: "plain" };
}

function riskTone(risk: number): "plain" | "good" | "warn" | "alert" {
  if (risk >= 70) return "alert";
  if (risk >= 40) return "warn";
  return "good";
}

type StateFilter = "all" | "online" | "offline" | "unverified" | "quarantined";

const STATE_FILTERS: { value: StateFilter; label: string }[] = [
  { value: "all", label: "All states" },
  { value: "online", label: "Online" },
  { value: "offline", label: "Offline" },
  { value: "unverified", label: "Unverified" },
  { value: "quarantined", label: "Quarantined" },
];

function matchesState(device: DeviceCard, filter: StateFilter): boolean {
  switch (filter) {
    case "online":
      return device.online;
    case "offline":
      return !device.online;
    case "unverified":
      return device.unverified;
    case "quarantined":
      return device.quarantined;
    default:
      return true;
  }
}

/**
 * The reference console's `#lan` table — the same devices as the card grid,
 * read as rows: kind · IP · Name · State · Risk · Services · MAC · Vendor ·
 * Label · Online 7d · First seen · Last seen · Network.
 *
 * The grid stays the default view; this is the other half of the reference's
 * map/table toggle, for the reader who wants to sort forty devices by risk or
 * by how much of the week they were actually on. `online_7d` is the hub's own
 * figure (`payloads/presence.online_pct`), the same one the Screen time page
 * reports for that device — `null` when nothing measured it, never 0%.
 */
export function DevicesTable({
  devices,
  onOpen,
  showNetwork = true,
}: {
  devices: DeviceCard[];
  onOpen: (mac: string) => void;
  showNetwork?: boolean;
}) {
  const [sort, setSort] = useState<SortState | null>(null);
  // The table's own filter, on top of whatever the search box above it
  // already narrowed -- a person sorting forty rows by risk usually also
  // wants to drop everything that is already offline or already trusted,
  // and that is a different question than the free-text search answers.
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");
  const filtered = devices.filter((d) => matchesState(d, stateFilter));

  const columns: Column<DeviceCard>[] = [
    {
      key: "kind",
      header: "Kind",
      render: (row) => (
        <span className="g-devtable-kind" title={row.kind}>
          <DeviceIcon icon={row.kind} size={18} />
          <span className="hint">{row.kind}</span>
        </span>
      ),
    },
    { key: "ip", header: "IP", render: (row) => <span className="num">{row.ip || "—"}</span> },
    {
      key: "name",
      header: "Name",
      render: (row) => <span className="num">{row.label || row.name || row.vendor || row.mac}</span>,
    },
    {
      key: "presence",
      header: "State",
      render: (row) => {
        const state = stateBadge(row);
        return <Badge tone={state.tone}>{state.label}</Badge>;
      },
    },
    {
      key: "risk",
      header: "Risk",
      align: "right",
      render: (row) => <Badge tone={riskTone(row.risk)}>{<span className="num">{row.risk}</span>}</Badge>,
    },
    {
      key: "ports",
      header: "Services",
      render: (row) =>
        row.ports.length === 0 ? (
          <span className="hint">—</span>
        ) : (
          <span className="num hint">{row.ports.map((p) => p.port).join(" ")}</span>
        ),
    },
    { key: "mac", header: "MAC", render: (row) => <span className="num">{row.mac}</span> },
    {
      key: "vendor",
      header: "Vendor",
      // A vendor string can run to 60 characters (a long OUI registrant
      // name) -- truncate deliberately rather than let it stretch the row,
      // with the full value still reachable as a `title` tooltip.
      render: (row) =>
        row.vendor ? (
          <span className="g-devtable-truncate" title={row.vendor}>
            {row.vendor}
          </span>
        ) : (
          <span className="hint">unknown</span>
        ),
    },
    {
      key: "label",
      header: "Label",
      render: (row) =>
        row.label ? (
          <span className="g-devtable-truncate" title={row.label}>
            {row.label}
          </span>
        ) : (
          <span className="hint">—</span>
        ),
    },
    {
      key: "online_7d",
      header: "Online 7d",
      align: "right",
      render: (row) =>
        row.online_7d === null ? (
          <span className="hint">—</span>
        ) : (
          <span className="num">{row.online_7d}%</span>
        ),
    },
    {
      key: "first_seen",
      header: "First seen",
      render: (row) => <span className="num hint">{row.first_seen === "" ? "—" : ago(row.first_seen)}</span>,
    },
    {
      key: "last_seen",
      header: "Last seen",
      render: (row) => <span className="num hint">{row.last_seen === "" ? "—" : ago(row.last_seen)}</span>,
    },
  ];

  if (showNetwork) {
    columns.push({
      key: "network",
      header: "Network",
      render: (row) => (row.network === null ? <span className="hint">—</span> : <NetworkBadge network={row.network} />),
    });
  }

  return (
    <div className="g-devtable-wrap">
      <label className="g-devtable-filter">
        <span>State</span>
        <select value={stateFilter} onChange={(e) => setStateFilter(e.target.value as StateFilter)}>
          {STATE_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </label>
      <Table
        columns={columns}
        rows={filtered}
        sort={sort}
        onSort={setSort}
        rowKey={(row) => row.mac}
        onRowClick={(row) => onOpen(row.mac)}
        empty={
          stateFilter === "all"
            ? "No devices match."
            : "No devices in this state. Try a different one above."
        }
        dense
        data-testid="devices-table"
      />
    </div>
  );
}

export default DevicesTable;
