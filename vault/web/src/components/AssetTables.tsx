import { useState } from "react";
import { Badge, Table } from "../design";
import type { Column, SortState } from "../design";
import { ms, num, pct, plural } from "../lib/format";
import type { HostCard, SiteCard } from "../types";

/** Mean of the card's own latency spark — the reference's "Avg latency 24h"
 *  column. The spark is the 24h series the hub already decimated, so this is
 *  the same window, not a second measurement. */
function avgLatency(spark: number[]): number | null {
  if (spark.length === 0) return null;
  return spark.reduce((a, b) => a + b, 0) / spark.length;
}

function certTone(days: number | null, warnDays: number): "plain" | "good" | "warn" | "alert" {
  if (days === null) return "plain";
  if (days <= 0) return "alert";
  if (days <= warnDays) return "warn";
  return "good";
}

/**
 * The reference console's websites table (`#sites`), column for column:
 * state · Site · HTTP · Latency · Cert · Issuer · Expires · IP address · Uptime 24h ·
 * Uptime 7d · Avg latency 24h.
 *
 * It sits under the cards rather than replacing them — the cards carry the
 * sparkline and the header pills — and it is the only place `cert_issuer`,
 * `cert_expires`, `dns_a`, `incidents_7d` and the HTTP status code render at
 * all.
 */
export function SitesTable({ sites, certWarnDays }: { sites: SiteCard[]; certWarnDays: number }) {
  const [sort, setSort] = useState<SortState | null>(null);

  const columns: Column<SiteCard>[] = [
    {
      key: "ok",
      header: "State",
      align: "center",
      render: (row) => (
        <span
          className={`g-dot g-cell-dot g-tone-${row.ok === false ? "alert" : row.ok === null ? "plain" : "good"}`}
          title={row.ok === false ? "down" : row.ok === null ? "not measured" : "up"}
        />
      ),
    },
    { key: "name", header: "Site", render: (row) => <span title={row.url}>{row.name}</span> },
    {
      key: "code",
      header: "HTTP",
      align: "right",
      render: (row) => <span className="num">{row.code === null ? "—" : row.code}</span>,
    },
    { key: "ms", header: "Latency", align: "right", render: (row) => <span className="num">{ms(row.ms)}</span> },
    {
      key: "cert_days",
      header: "Cert",
      align: "right",
      render: (row) => (
        <span className={`num g-tone-${certTone(row.cert_days, certWarnDays)}`}>
          {row.cert_days === null ? "—" : `${row.cert_days}d`}
        </span>
      ),
    },
    { key: "cert_issuer", header: "Issuer", render: (row) => row.cert_issuer || "—" },
    {
      key: "cert_expires",
      header: "Expires",
      render: (row) => <span className="num hint">{row.cert_expires || "—"}</span>,
    },
    {
      key: "dns_a",
      // Item 12: this column is the A records -- the addresses the site
      // resolves to. Calling it "DNS" named the lookup rather than the
      // answer, and beside a DNS-filter tab that means something else
      // entirely it read as a third thing again.
      header: "IP address",
      render: (row) =>
        row.dns_a.length === 0 ? (
          <span className="hint">—</span>
        ) : (
          <span className="num" title={row.dns_a.join(", ")}>
            {row.dns_a[0]}
            {row.dns_a.length > 1 && <span className="hint"> +{row.dns_a.length - 1}</span>}
          </span>
        ),
    },
    {
      key: "uptime_24h",
      header: "Uptime 24h",
      align: "right",
      render: (row) => <span className="num">{pct(row.uptime_24h)}</span>,
    },
    {
      key: "uptime_7d",
      header: "Uptime 7d",
      align: "right",
      render: (row) => <span className="num">{pct(row.uptime_7d)}</span>,
    },
    {
      key: "avg",
      header: "Avg latency 24h",
      align: "right",
      render: (row) => <span className="num">{ms(avgLatency(row.latency_spark))}</span>,
    },
    {
      key: "incidents_7d",
      header: "Incidents 7d",
      align: "right",
      render: (row) =>
        row.incidents_7d === 0 ? (
          <span className="num hint">0</span>
        ) : (
          <span className="num g-tone-warn">{row.incidents_7d}</span>
        ),
    },
  ];

  return (
    <Table
      columns={columns}
      rows={sites}
      sort={sort}
      onSort={setSort}
      rowKey={(row) => row.name}
      empty="No sites are being watched yet."
    />
  );
}

/**
 * The reference console's servers table (`#hosts`), column for column:
 * state · Host · Uptime · Disk · Mem · Load/core · fail2ban · Banned ·
 * Failed SSH 24h · Updates · Ports · Containers / services.
 *
 * Same placement the reference gives it — collapsed under the cards, which
 * are the primary presentation there too. This is the only place `uptime`,
 * `load_per_core`, `fail2ban`, `banned` and `failed_ssh_24h` render.
 */
export function HostsTable({ hosts }: { hosts: HostCard[] }) {
  const [sort, setSort] = useState<SortState | null>(null);

  const columns: Column<HostCard>[] = [
    {
      key: "reachable",
      header: "State",
      align: "center",
      render: (row) => (
        <span
          className={`g-dot g-cell-dot g-tone-${row.reachable === false ? "alert" : row.reachable === null ? "plain" : "good"}`}
          title={row.reachable === false ? "unreachable" : row.reachable === null ? "not measured" : "reachable"}
        />
      ),
    },
    { key: "name", header: "Host", render: (row) => <span title={row.address || row.hostname}>{row.name}</span> },
    { key: "uptime", header: "Uptime", render: (row) => <span className="num hint">{row.uptime || "—"}</span> },
    {
      key: "disk_pct",
      header: "Disk",
      align: "right",
      render: (row) => <span className="num">{pct(row.disk_pct)}</span>,
    },
    {
      key: "mem_pct",
      header: "Mem",
      align: "right",
      render: (row) => <span className="num">{pct(row.mem_pct)}</span>,
    },
    {
      key: "load_per_core",
      header: "Load/core",
      align: "right",
      render: (row) => (
        <span className="num" title={row.load1 === null ? undefined : `load1 ${row.load1}`}>
          {row.load_per_core === null ? "—" : row.load_per_core.toFixed(2)}
        </span>
      ),
    },
    {
      key: "fail2ban",
      header: "fail2ban",
      align: "center",
      // Never reported is an em dash — "off" would be a claim about the host.
      render: (row) =>
        row.fail2ban === null ? (
          <span className="hint">—</span>
        ) : (
          <Badge tone={row.fail2ban ? "good" : "warn"}>{row.fail2ban ? "on" : "off"}</Badge>
        ),
    },
    {
      key: "banned",
      header: "Banned",
      align: "right",
      render: (row) => <span className="num">{num(row.banned)}</span>,
    },
    {
      key: "failed_ssh_24h",
      header: "Failed SSH 24h",
      align: "right",
      render: (row) =>
        row.failed_ssh_24h === 0 ? (
          <span className="num hint">0</span>
        ) : (
          <span className="num g-tone-warn">{num(row.failed_ssh_24h)}</span>
        ),
    },
    {
      key: "pending_updates",
      header: "Updates",
      align: "right",
      render: (row) => (
        <span className="num" title={`${row.security_updates} of them security updates`}>
          {num(row.pending_updates)}
          {row.security_updates > 0 && <span className="g-tone-alert"> ({row.security_updates})</span>}
        </span>
      ),
    },
    {
      key: "ports",
      header: "Ports",
      render: (row) =>
        row.ports.length === 0 ? (
          <span className="hint">—</span>
        ) : (
          <span
            className={`num g-tone-${row.ports.some((p) => p.risky) ? "warn" : "plain"}`}
            title={row.ports.map((p) => `${p.port}${p.service ? ` ${p.service}` : ""}`).join(", ")}
          >
            {row.ports.map((p) => p.port).join(" ")}
          </span>
        ),
    },
    {
      key: "docker",
      header: "Containers / services",
      render: (row) => {
        const total = row.docker.length + row.services.length;
        if (total === 0) return <span className="hint">—</span>;
        return (
          <span
            title={[
              ...row.docker.map((c) => `${c.name} · ${c.status}`),
              ...row.services.map((s) => `${s.name} · ${s.state}`),
            ].join("\n")}
          >
            {plural(total, "unit")}
          </span>
        );
      },
    },
  ];

  return (
    <Table
      columns={columns}
      rows={hosts}
      sort={sort}
      onSort={setSort}
      rowKey={(row) => row.name}
      empty="No hosts are being watched yet."
    />
  );
}
