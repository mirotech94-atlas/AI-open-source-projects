import { useState } from "react";
import { Badge, Table } from "../design";
import type { Column, SortState } from "../design";
import { CountBars } from "./CountBars";
import { num } from "../lib/format";
import type { DnsClient, DnsPayload, NameCount } from "../types";

export type DnsInsightsProps = {
  top: NameCount[];
  blockedTop: NameCount[];
  clients: DnsClient[];
};

/** A beaconing or odd-looking domain, with the reason it is listed. Both
 *  arrive from the hub as plain `{name, count}`, so the pattern column names
 *  which list the row came from rather than inventing an interval Vault
 *  does not measure. */
type OddRow = NameCount & { pattern: "beaconing" | "suspicious" };

const PATTERN_LABEL: Record<OddRow["pattern"], string> = {
  beaconing: "fixed timer",
  suspicious: "random-looking",
};

function oddRows(payload: Pick<DnsPayload, "beaconing" | "suspicious">): OddRow[] {
  return [
    ...payload.beaconing.map((n) => ({ ...n, pattern: "beaconing" as const })),
    ...payload.suspicious.map((n) => ({ ...n, pattern: "suspicious" as const })),
  ];
}

/** The reference's three DNS lists: most looked-up, most blocked, busiest
 *  devices — every one of them from payload fields the page already fetched. */
export function DnsInsights({ top, blockedTop, clients }: DnsInsightsProps) {
  return (
    <div className="g-dns-lists-row">
      <section>
        <h3>Most looked-up domains</h3>
        <CountBars
          rows={top.map((n) => ({ name: n.name, count: n.count }))}
          empty="No queries recorded yet."
          testId="dns-top"
        />
      </section>
      <section>
        <h3>Most blocked</h3>
        <CountBars
          rows={blockedTop.map((n) => ({ name: n.name, count: n.count }))}
          tone="alert"
          empty="Nothing blocked yet."
          testId="dns-blocked"
        />
      </section>
      <section>
        <h3>Busiest devices</h3>
        <CountBars
          rows={clients.map((c) => ({
            id: c.mac || c.ip || c.name,
            name: deviceLabel(c, clients),
            count: c.queries,
            title: `${c.name || c.ip} — ${num(c.queries)} queries, ${num(c.blocked)} blocked`,
          }))}
          empty="No device has asked yet."
          testId="dns-clients"
        />
      </section>
    </div>
  );
}

/** A device's name, with its address added only when another device in the
 *  same list carries the same name -- otherwise two "iPhone" bars are two
 *  facts the owner cannot tell apart. */
function deviceLabel(c: DnsClient, all: DnsClient[]): string {
  const name = c.name || c.ip || c.mac || "unknown";
  const twins = c.name ? all.filter((o) => o.name === c.name).length : 0;
  return twins > 1 && c.ip ? `${name} · ${c.ip}` : name;
}

/** "Beaconing & suspicious lookups": a device contacting one domain on a
 *  fixed timer, or a random-looking domain. */
export function DnsOddLookups({ payload }: { payload: Pick<DnsPayload, "beaconing" | "suspicious"> }) {
  const [sort, setSort] = useState<SortState | null>(null);
  const rows = oddRows(payload);

  const columns: Column<OddRow>[] = [
    { key: "name", header: "Domain", render: (row) => <span className="num">{row.name}</span> },
    {
      key: "count",
      header: "Lookups",
      align: "right",
      render: (row) => <span className="num">{num(row.count)}</span>,
    },
    {
      key: "pattern",
      header: "Pattern",
      render: (row) => (
        <Badge tone={row.pattern === "beaconing" ? "warn" : "plain"}>{PATTERN_LABEL[row.pattern]}</Badge>
      ),
    },
  ];

  return (
    <Table
      columns={columns}
      rows={rows}
      sort={sort}
      onSort={setSort}
      rowKey={(row) => `${row.pattern}|${row.name}`}
      empty="Nothing unusual — no beaconing and no odd domains today."
    />
  );
}

export default DnsInsights;
