import { machineClass } from "../lib/machine";
import { useLiveFeed } from "../lib/live";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Card, Page, PageHeader, StatStrip, Table, Tabs } from "../design";
import { ExportMenu } from "../components/ExportMenu";
import type { StatItem } from "../design";
import type { Column } from "../design";
import { EmptyState } from "../components/EmptyState";
import { NetworkBadge } from "../components/NetworkBadge";
import { TrendPanels } from "../components/TrendPanels";
import { historyPayload } from "../lib/api";
import { useMe } from "../lib/auth";
import { clock, day } from "../lib/format";
import { useSelectedNetwork } from "../lib/network";
import type { HistoryEventRow, HistoryPayload, HistoryScanRow } from "../types";

// The five ranges the reference console offers, in hours — its own unit. The
// hub takes `days=` for whole-day windows and `hours=` for anything shorter
// (`days` cannot go below 1), so 6h is the one value that travels as hours and
// gets the hub's 5-minute buckets instead of hourly means.
const RANGES = [
  { value: "6", label: "6h" },
  { value: "24", label: "24h" },
  { value: "72", label: "3d" },
  { value: "168", label: "7d" },
  { value: "720", label: "30d" },
];

const EMPTY: HistoryPayload = {
  days: 7,
  hours: 0,
  network: null,
  scan_count: 0,
  first_ts: null,
  last_ts: null,
  charts: [],
  scans: [],
  events: [],
};

function stamp(ts: string): string {
  return `${day(ts)} ${clock(ts)}`;
}

/** A cluster of events close enough together to read as one moment — a
 *  device rescan that flips five ports at once should not read as five
 *  unrelated rows. Anything within this window of the row before it (in the
 *  hub's own, already-descending order) joins the same cluster; the label on
 *  the merged row is the newest timestamp in it, since a reader scanning
 *  backwards is looking for when a moment *started* showing up, not when its
 *  last echo landed. */
const CLUSTER_WINDOW_MS = 60_000;

type EventDisplayRow = HistoryEventRow & { clusterOf?: HistoryEventRow[] };

function clusterEvents(events: HistoryEventRow[]): EventDisplayRow[] {
  const out: EventDisplayRow[] = [];
  for (const e of events) {
    const last = out[out.length - 1];
    const lastTs = last ? new Date(last.ts).getTime() : NaN;
    if (last && Math.abs(lastTs - new Date(e.ts).getTime()) <= CLUSTER_WINDOW_MS) {
      if (!last.clusterOf) last.clusterOf = [{ ...last }];
      last.clusterOf.push(e);
    } else {
      out.push({ ...e });
    }
  }
  return out;
}

/** Splits an already-descending list into same-calendar-day groups without
 *  reordering anything — a plain re-bucketing, not a sort, so "newest first"
 *  inside a day and "most recent day first" across days both fall out of the
 *  hub's own ordering. */
function groupByDay<T extends { ts: string }>(rows: T[]): { label: string; rows: T[] }[] {
  const groups: { label: string; rows: T[] }[] = [];
  for (const row of rows) {
    const label = day(row.ts);
    const current = groups[groups.length - 1];
    if (current && current.label === label) current.rows.push(row);
    else groups.push({ label, rows: [row] });
  }
  return groups;
}

const SCAN_COLUMNS: Column<HistoryScanRow>[] = [
  { key: "ts", header: "Time", render: (r) => <span className="num hint g-history-when">{clock(r.ts)}</span> },
  {
    key: "sites",
    header: "Sites",
    align: "right",
    render: (r) => (
      <span className="num">
        {r.sites_ok}
        <span className="hint">/{r.sites_total}</span>
      </span>
    ),
  },
  {
    key: "hosts",
    header: "Hosts",
    align: "right",
    render: (r) => (
      <span className="num">
        {r.hosts_ok}
        <span className="hint">/{r.hosts_total}</span>
      </span>
    ),
  },
  {
    key: "devices",
    header: "Devices",
    align: "right",
    render: (r) => <span className="num">{r.devices === null ? "—" : r.devices}</span>,
  },
  {
    key: "score",
    header: "Score",
    align: "right",
    render: (r) => <span className="num">{r.score === null ? "—" : r.score}</span>,
  },
  {
    key: "alerts",
    header: "Alerts",
    align: "right",
    render: (r) =>
      r.crit === 0 && r.warn === 0 ? (
        <span className="hint">—</span>
      ) : (
        <span className="g-history-sevs">
          {r.crit > 0 && <span className="g-history-sev g-tone-alert num">{r.crit} crit</span>}
          {r.warn > 0 && <span className="g-history-sev g-tone-warn num">{r.warn} warn</span>}
        </span>
      ),
  },
];

const EVENT_COLUMNS: Column<EventDisplayRow>[] = [
  {
    key: "ts",
    header: "Time",
    render: (r) => <span className="num hint g-history-when">{clock(r.ts)}</span>,
  },
  {
    key: "kind",
    header: "What happened",
    render: (r) =>
      !r.clusterOf ? (
        r.kind
      ) : (
        // A burst of near-simultaneous events collapses to one row so it
        // reads as one thing that happened, not a wall of duplicate rows —
        // the individual events are still there, one click away.
        <details className="g-history-cluster">
          <summary>{r.clusterOf.length} changes at once</summary>
          <ul>
            {r.clusterOf.map((item) => (
              <li key={`${item.ts}-${item.kind}-${item.entity}`}>
                <span className="hint">{clock(item.ts)}</span> {item.kind}
                {item.entity !== "" && (
                  <>
                    {" — "}
                    {/* The thing the event was about: a device name when it
                        has one, its MAC when it does not. */}
                    <Link to={item.href} className={machineClass(item.entity)}>{item.entity}</Link>
                  </>
                )}
                {/* The detail renders as its own text node inside the
                    parentheses, so it is marked rather than the whole span --
                    the brackets are punctuation and stay in the prose face. */}
                {item.detail !== "" && (
                  <span className="hint">
                    {" ("}<span className={machineClass(item.detail)}>{item.detail}</span>{")"}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </details>
      ),
  },
  {
    key: "entity",
    header: "Where",
    render: (r) =>
      r.clusterOf || r.entity === "" ? (
        <span className="hint">—</span>
      ) : (
        <Link to={r.href} className="num">
          {r.entity}
        </Link>
      ),
  },
  {
    key: "detail",
    header: "Detail",
    // A detail is a phrase for most events and a bare address for device
    // ones, through the same cell.
    render: (r) => {
      const v = r.clusterOf ? "—" : r.detail || "—";
      return <span className={`hint ${machineClass(v)}`.trim()}>{v}</span>;
    },
  },
  {
    key: "network",
    header: "Network",
    render: (r) => (r.network === null ? <span className="hint">—</span> : <NetworkBadge network={r.network} />),
  },
];

/**
 * History: what changed and how the numbers moved, over a chosen range.
 *
 * Read-only by design — nothing on this tab mutates anything, so every role
 * sees the identical page. The hub does all the bucketing (see
 * `payloads/history.py`); this page picks a range, lets the reader choose which
 * series to compare in a crowded panel, and draws.
 */
export function History() {
  const [range, setRange] = useState("24");
  const hours = Number(range);
  // Whole-day windows keep asking in days, exactly as before; only a sub-day
  // window uses `hours=`.
  const subDay = hours < 24;
  const meQuery = useMe();
  // Reuses the tenant-scoped selection every other network-aware tab reads,
  // so switching networks on Assets carries into History.
  const [network] = useSelectedNetwork(meQuery.data?.active_tenant?.id ?? null);

  // Item 13/49: History is the record of what happened, and it had neither
  // a poll nor a live handler -- a tab left open showed the estate as it
  // was when it was opened, with no hint that anything since had been
  // missed. A finished sweep is the only thing that adds to it.
  const live = useLiveFeed();
  const queryClient = useQueryClient();
  useEffect(() => {
    return live.on("scan", () => {
      void queryClient.invalidateQueries({ queryKey: ["history"] });
    });
  }, [live, queryClient]);

  const query = useQuery({
    queryKey: ["history", range, network],
    queryFn: () => historyPayload(subDay ? 1 : hours / 24, network, subDay ? hours : 0),
  });

  const payload = query.data ?? EMPTY;
  const hasAnything = payload.charts.length > 0 || payload.scans.length > 0 || payload.events.length > 0;
  const scanDays = groupByDay(payload.scans);
  const eventDays = groupByDay(clusterEvents(payload.events));

  const fixesApplied = payload.events.filter((e) => /fix/i.test(e.kind)).length;
  const histStats: StatItem[] = [
    { label: "Scans in range", value: String(payload.scan_count) },
    { label: "Events", value: String(payload.events.length) },
    { label: "Fixes applied", value: String(fixesApplied), deltaTone: "good" },
    { label: "Latest", value: payload.last_ts === null ? "—" : stamp(payload.last_ts) },
  ];
  return (
    <Page>
      <PageHeader
        eyebrow="Record · the evidence trail"
        title="History"
        sub="Every scan and every change in the window you pick, newest first — the record you reach for after something happens."
        extra={
          <>
            <Tabs items={RANGES} value={range} onChange={setRange} label="History range" />
            <ExportMenu path="/api/v1/export/history" query={{ days: Math.max(1, Math.round(hours / 24)) }} />
          </>
        }
      />
      <StatStrip items={histStats} />
    <div className="g-history">
      <p className="hint num">
        {payload.scan_count} {payload.scan_count === 1 ? "scan" : "scans"} in range
        {payload.last_ts !== null && ` · latest ${stamp(payload.last_ts)}`}
      </p>

      {query.isPending && <p className="hint">Loading history…</p>}

      {/* `first_ts === null` means Vault has never completed a scan at
          all, on any range — a different fact from "nothing happened in
          this window", and one the reader should not have to infer from a
          blank page that looks identical either way. */}
      {!query.isPending && !hasAnything && payload.first_ts === null && (
        <EmptyState
          title="Vault has just started."
          body="There is no history yet because there has not been a first scan yet — this page fills in on its own once one completes. Nothing is missing or broken."
        />
      )}

      {!query.isPending && !hasAnything && payload.first_ts !== null && (
        <EmptyState
          title="Nothing changed in this window."
          body="Vault has history from earlier scans — widen the range above to see it."
        />
      )}

      <TrendPanels charts={payload.charts} label="How the numbers moved" />

      {payload.scans.length > 0 && (
        <Card title="Scan log" subtitle={`The last ${payload.scans.length} scans, newest first.`}>
          <div className="g-history-timeline">
            {/* Item 39: a day is a fold, not a heading. Thirty days of
                every-few-minutes scans is thousands of rows, and a wall you
                scroll past is not a log you read. The newest day is open
                because that is the one anybody came for. */}
            {scanDays.map((group, i) => (
              <details className="g-history-day" key={group.label} open={i === 0}>
                <summary className="g-history-day-label">
                  {group.label}
                  <span className="g-history-day-n num">{group.rows.length}</span>
                </summary>
                <Table columns={SCAN_COLUMNS} rows={group.rows} rowKey={(r) => r.ts} dense />
              </details>
            ))}
          </div>
        </Card>
      )}

      {payload.events.length > 0 && (
        <Card title="Event log" subtitle="Devices, ports, WAN changes and applied fixes — newest first, a burst at one moment shown as one row.">
          <div className="g-history-timeline">
            {eventDays.map((group, i) => (
              <details className="g-history-day" key={group.label} open={i === 0}>
                <summary className="g-history-day-label">
                  {group.label}
                  <span className="g-history-day-n num">{group.rows.length}</span>
                </summary>
                <Table
                  columns={EVENT_COLUMNS}
                  rows={group.rows}
                  rowKey={(r) => (r.clusterOf ? `cluster-${r.ts}` : `${r.ts}-${r.kind}-${r.entity}`)}
                  dense
                />
              </details>
            ))}
          </div>
        </Card>
      )}
    </div>
    </Page>
  );
}

export default History;
