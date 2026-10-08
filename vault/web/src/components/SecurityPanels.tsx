import { useMemo, useState } from "react";
import { Badge, Table } from "../design";
import type { Column } from "../design";
import { ago, clock, day } from "../lib/format";
import type { EmailAuthRow, ExtPortRow, FixLogRow, WifiGuard } from "../types";

/** SPF / DKIM / DMARC per monitored domain — the reference's "Email spoofing
 *  protection". A domain that sends no mail (`has_mx` false) still needs a
 *  hard SPF and an enforcing DMARC so nobody can send as it, which is why the
 *  row says so rather than excusing it. */
const EMAIL_COLUMNS: Column<EmailAuthRow>[] = [
  {
    key: "domain",
    header: "Domain",
    render: (row) => (
      <span className="num">
        {row.domain}
        {!row.has_mx && <span className="hint"> · sends no mail</span>}
      </span>
    ),
  },
  {
    key: "spf",
    header: "SPF",
    render: (row) =>
      row.spf === "" ? (
        <Badge tone="warn">missing</Badge>
      ) : (
        <span className="num hint" title={row.spf}>
          published
        </span>
      ),
  },
  {
    key: "dkim_selector",
    header: "DKIM",
    render: (row) =>
      row.dkim_selector === "" ? <span className="hint">no selector answered</span> : <span className="num">{row.dkim_selector}</span>,
  },
  {
    key: "dmarc",
    header: "DMARC",
    render: (row) => (row.dmarc === "" ? <Badge tone="warn">missing</Badge> : <span className="hint">published</span>),
  },
  {
    key: "dmarc_policy",
    header: "Policy",
    render: (row) =>
      row.dmarc_policy === "" ? (
        <span className="hint">—</span>
      ) : (
        <Badge tone={row.dmarc_policy === "none" ? "warn" : "good"}>
          <span className="num">p={row.dmarc_policy}</span>
        </Badge>
      ),
  },
  {
    key: "passed",
    header: "Spoofable",
    render: (row) => <Badge tone={row.passed ? "good" : "warn"}>{row.passed ? "no" : "yes"}</Badge>,
  },
  {
    key: "checked",
    header: "Checked",
    render: (row) => <span className="num hint">{row.checked === "" ? "—" : ago(row.checked)}</span>,
  },
];

export function EmailAuth({ rows }: { rows: EmailAuthRow[] }) {
  return (
    <Table
      columns={EMAIL_COLUMNS}
      rows={rows}
      rowKey={(row) => row.domain}
      dense
      empty="No domains are being checked for spoofing protection."
    />
  );
}

/** What the internet answers on, per site host — the reference's "Site hosts
 *  seen from the internet", probed from a VPS rather than from inside. */
const EXT_PORT_COLUMNS: Column<ExtPortRow>[] = [
  { key: "host", header: "Host", render: (row) => <span className="num">{row.host}</span> },
  { key: "ip", header: "IP", render: (row) => <span className="num">{row.ip || "—"}</span> },
  {
    key: "ports",
    header: "Open ports",
    render: (row) =>
      row.ports.length === 0 ? (
        <span className="hint">nothing answered</span>
      ) : (
        <span className="g-secpanel-ports">
          {row.ports.map((port) => (
            <span key={port} className={row.unexpected.includes(port) ? "num g-tone-warn" : "num hint"}>
              {port}
            </span>
          ))}
        </span>
      ),
  },
  {
    key: "findings",
    header: "Admin / leaks",
    render: (row) =>
      row.findings.length === 0 ? (
        <Badge tone="good">clean</Badge>
      ) : (
        <span className="g-secpanel-ports">
          {row.findings.map((path) => (
            <span key={path} className="num g-tone-alert">
              {path}
            </span>
          ))}
        </span>
      ),
  },
  {
    key: "checked",
    header: "Probed",
    render: (row) => (
      <span className="num hint">
        {row.checked === "" ? "—" : ago(row.checked)}
        {row.probe_host !== "" && <span className="hint"> from {row.probe_host}</span>}
      </span>
    ),
  },
];

export function ExternalPorts({ rows }: { rows: ExtPortRow[] }) {
  return (
    <Table
      columns={EXT_PORT_COLUMNS}
      rows={rows}
      rowKey={(row) => row.host}
      dense
      empty="No site host has been probed from the internet yet."
    />
  );
}

/** Every repair Vault ran, automatic or asked for. */
const FIX_LOG_COLUMNS: Column<FixLogRow>[] = [
  {
    key: "ts",
    header: "When",
    render: (row) => (
      <span className="num hint">
        {day(row.ts)} {clock(row.ts)}
      </span>
    ),
  },
  { key: "host", header: "Host", render: (row) => <span className="num">{row.host || "—"}</span> },
  {
    key: "action",
    header: "Action",
    render: (row) => (
      <span>
        <span className="num">{row.action}</span>
        {row.label !== "" && row.label !== row.action && <span className="hint"> · {row.label}</span>}
      </span>
    ),
  },
  { key: "by", header: "By", render: (row) => <span className="num hint">{row.by}</span> },
  {
    key: "ok",
    header: "Result",
    render: (row) => (
      <span>
        <Badge tone={row.ok ? "good" : "alert"}>{row.ok ? "succeeded" : "failed"}</Badge>
        {row.detail !== "" && <span className="hint"> {row.detail}</span>}
      </span>
    ),
  },
];

/** Item 28: the repair log, filterable.
 *
 *  It was a bare table, so finding "what did Vault do to vps1 last
 *  Tuesday, and did it work" meant reading every row. The four filters are
 *  the four columns a person actually narrows by -- which machine, what
 *  kind of repair, who asked for it, and whether it worked -- plus free
 *  text over the rest. Every one is local to the rows already fetched. */
export function FixLog({ rows }: { rows: FixLogRow[] }) {
  const [host, setHost] = useState("all");
  const [action, setAction] = useState("all");
  const [by, setBy] = useState("all");
  const [result, setResult] = useState<"all" | "ok" | "failed">("all");
  const [text, setText] = useState("");

  const hosts = useMemo(() => [...new Set(rows.map((r) => r.host).filter(Boolean))].sort(), [rows]);
  const actions = useMemo(() => [...new Set(rows.map((r) => r.action).filter(Boolean))].sort(), [rows]);
  const people = useMemo(() => [...new Set(rows.map((r) => r.by).filter(Boolean))].sort(), [rows]);

  const shown = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return rows.filter((r) => {
      if (host !== "all" && r.host !== host) return false;
      if (action !== "all" && r.action !== action) return false;
      if (by !== "all" && r.by !== by) return false;
      if (result === "ok" && !r.ok) return false;
      if (result === "failed" && r.ok) return false;
      if (needle === "") return true;
      return (
        r.label.toLowerCase().includes(needle) ||
        r.detail.toLowerCase().includes(needle) ||
        r.action.toLowerCase().includes(needle) ||
        r.host.toLowerCase().includes(needle)
      );
    });
  }, [rows, host, action, by, result, text]);

  return (
    <>
      {rows.length > 0 && (
        <div className="g-fixlog-controls">
          <label className="g-cve-pick">
            <span>Machine</span>
            <select aria-label="Filter repairs by machine" value={host} onChange={(e) => setHost(e.target.value)}>
              <option value="all">Any</option>
              {hosts.map((h) => <option key={h} value={h}>{h}</option>)}
            </select>
          </label>
          <label className="g-cve-pick">
            <span>Repair</span>
            <select aria-label="Filter repairs by action" value={action} onChange={(e) => setAction(e.target.value)}>
              <option value="all">Any</option>
              {actions.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </label>
          <label className="g-cve-pick">
            <span>Asked by</span>
            <select aria-label="Filter repairs by who asked" value={by} onChange={(e) => setBy(e.target.value)}>
              <option value="all">Anyone</option>
              {people.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <label className="g-cve-pick">
            <span>Result</span>
            <select aria-label="Filter repairs by result" value={result}
                    onChange={(e) => setResult(e.target.value as typeof result)}>
              <option value="all">Any</option>
              <option value="ok">Worked</option>
              <option value="failed">Failed</option>
            </select>
          </label>
          <label className="g-cve-filter">
            <span>Find</span>
            <input type="search" aria-label="Search repairs" value={text}
                   onChange={(e) => setText(e.target.value)} placeholder="what it did, or why…" />
          </label>
          <span className="g-cve-shown hint num">{shown.length} of {rows.length}</span>
        </div>
      )}
      <Table
        columns={FIX_LOG_COLUMNS}
        rows={shown}
        rowKey={(row) => `${row.ts}-${row.host}-${row.action}`}
        dense
        empty={rows.length === 0
          ? "Vault has not had to repair anything yet."
          : "No repair matches these filters."}
      />
    </>
  );
}

/**
 * The reference's "Rogue network checks" box, read off the Wi-Fi guard rather
 * than from a second payload field: the DHCP server, the route actually in
 * use, and the MAC answering as the router. A changed router MAC on an
 * unchanged address is what ARP spoofing looks like, which is why the MAC is
 * here at all.
 *
 * Deliberately not a fourth copy of "evil twin detected" — that verdict and its
 * checks are the guard's own rows, right above this.
 */
export function RogueChecks({ wifi }: { wifi: WifiGuard }) {
  const expected = wifi.expected_gateway;
  const rows: { label: string; value: string; tone: "good" | "warn" | "alert" | "plain"; note: string }[] = [
    {
      label: "DHCP server",
      value: wifi.dhcp_server,
      tone: wifi.dhcp_server === "" ? "plain" : wifi.rogue_dhcp ? "alert" : "good",
      note: wifi.dhcp_server === "" ? "" : wifi.rogue_dhcp ? `not the router (${expected || "unknown"})` : "the router",
    },
    {
      label: "Default route",
      value: wifi.default_gateway,
      tone:
        wifi.default_gateway === ""
          ? "plain"
          : expected !== "" && wifi.default_gateway !== expected
            ? "alert"
            : "good",
      note:
        wifi.default_gateway !== "" && expected !== "" && wifi.default_gateway !== expected
          ? `expected ${expected}`
          : "",
    },
    { label: "Router MAC", value: wifi.gateway_mac, tone: "plain", note: "" },
  ];

  return (
    <dl className="g-facts" data-testid="rogue-checks">
      {rows.map((row) => (
        <div key={row.label} className="g-facts-row">
          <dt>{row.label}</dt>
          <dd>
            {row.value === "" ? (
              <span className="hint">not reported on this network</span>
            ) : (
              <>
                <Badge tone={row.tone}>
                  <span className="num">{row.value}</span>
                </Badge>
                {row.note !== "" && <span className="hint"> {row.note}</span>}
              </>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
