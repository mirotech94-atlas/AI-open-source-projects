import { useMemo, useState } from "react";
import { Table } from "../design";
import type { Column, SortState } from "../design";
import type { Tone } from "../lib/format";
import type { CveRow } from "../types";

export type CveTableProps = {
  cves: CveRow[];
};

const CRITICAL_URGENCIES = new Set(["high", "critical"]);

function urgencyTone(urgency: string): Tone {
  const norm = urgency.trim().toLowerCase();
  if (norm === "critical") return "alert";
  if (norm === "high") return "warn";
  return "plain";
}

/** The CVE table: CVE / package / urgency / host / fixable / fixed-on /
 *  tracker link, with filters on severity, server and state beside the
 *  free-text one. Every filter and the sort are local to the rows the hub
 *  already sent — never a refetch.
 *
 *  "Still open" is the default because a list that leads with already-fixed
 *  rows buries the ones that are not. */
export function CveTable({ cves }: CveTableProps) {
  // Item 32: "not just by name". Urgency, which server, and whether it is
  // still outstanding are the three questions a person actually arrives
  // with; free text alone made them scroll.
  const [urgency, setUrgency] = useState<"all" | "critical" | "medium" | "low">("all");
  const [host, setHost] = useState<string>("all");
  const [state, setState] = useState<"all" | "open" | "fixed" | "fixable">("open");
  const [text, setText] = useState("");
  const [sort, setSort] = useState<SortState | null>(null);

  const hostNames = useMemo(
    () => [...new Set(cves.map((c) => c.host).filter(Boolean))].sort(),
    [cves],
  );

  // A bare count of CVEs is not a fact a person can act on — how many hosts
  // carry them, and how many of those are fixable right now by the update
  // already running, is. Computed off the rows the hub already sent, never
  // a second fetch.
  const summary = useMemo(() => {
    const hosts = new Set(cves.map((c) => c.host));
    const criticalHosts = new Set(
      cves.filter((c) => CRITICAL_URGENCIES.has(c.urgency.trim().toLowerCase())).map((c) => c.host),
    );
    const fixableNow = cves.filter((c) => c.fixable).length;
    return { hostCount: hosts.size, criticalHostCount: criticalHosts.size, fixableNow };
  }, [cves]);

  const filtered = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return cves.filter((c) => {
      const u = c.urgency.trim().toLowerCase();
      if (urgency === "critical" && !CRITICAL_URGENCIES.has(u)) return false;
      if (urgency === "medium" && u !== "medium") return false;
      if (urgency === "low" && !(u === "low" || u === "negligible" || u === "unimportant")) return false;
      if (host !== "all" && c.host !== host) return false;
      if (state === "open" && c.fixed_on !== "") return false;
      if (state === "fixed" && c.fixed_on === "") return false;
      if (state === "fixable" && !c.fixable) return false;
      if (needle === "") return true;
      return (
        c.cve.toLowerCase().includes(needle) ||
        c.package.toLowerCase().includes(needle) ||
        c.host.toLowerCase().includes(needle)
      );
    });
  }, [cves, urgency, host, state, text]);

  const columns: Column<CveRow>[] = [
    {
      key: "cve",
      header: "CVE",
      render: (row) => (
        <span className="mono" data-testid="cve-row">
          {row.cve}
        </span>
      ),
    },
    { key: "package", header: "Package" },
    {
      key: "urgency",
      header: "Urgency",
      render: (row) => <span className={`g-badge g-tone-${urgencyTone(row.urgency)}`}>{row.urgency}</span>,
    },
    { key: "host", header: "Host" },
    { key: "fixable", header: "Fixable", align: "center", render: (row) => (row.fixable ? "Yes" : "No") },
    { key: "fixed_on", header: "Fixed on", render: (row) => row.fixed_on || "—" },
    {
      key: "tracker",
      header: "Tracker",
      render: (row) =>
        row.tracker ? (
          <a href={row.tracker} target="_blank" rel="noopener noreferrer">
            View
          </a>
        ) : (
          "—"
        ),
    },
  ];

  return (
    <div className="g-cve-table">
      {cves.length > 0 && (
        <p className="g-cve-summary">
          <span className="num">{cves.length}</span> CVE{cves.length === 1 ? "" : "s"} across{" "}
          <span className="num">{summary.hostCount}</span> host{summary.hostCount === 1 ? "" : "s"}
          {summary.criticalHostCount > 0 && (
            <>
              , <span className="num g-tone-alert">{summary.criticalHostCount}</span> of them at high or critical
              urgency
            </>
          )}
          . The daily upgrade already fixes <span className="num">{summary.fixableNow}</span> of these.
        </p>
      )}
      <div className="g-cve-controls">
        <label className="g-cve-filter">
          <span>Filter</span>
          <input
            type="search"
            aria-label="Filter CVEs"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="CVE, package or host…"
          />
        </label>
        <label className="g-cve-pick">
          <span>Severity</span>
          <select aria-label="Filter by severity" value={urgency}
                  onChange={(e) => setUrgency(e.target.value as typeof urgency)}>
            <option value="all">Any</option>
            <option value="critical">Critical &amp; high</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label className="g-cve-pick">
          <span>Server</span>
          <select aria-label="Filter by server" value={host}
                  onChange={(e) => setHost(e.target.value)}>
            <option value="all">Every server</option>
            {hostNames.map((h) => <option key={h} value={h}>{h}</option>)}
          </select>
        </label>
        <label className="g-cve-pick">
          <span>State</span>
          <select aria-label="Filter by state" value={state}
                  onChange={(e) => setState(e.target.value as typeof state)}>
            <option value="open">Still open</option>
            <option value="fixable">Fixable now</option>
            <option value="fixed">Already fixed</option>
            <option value="all">Any state</option>
          </select>
        </label>
        <span className="g-cve-shown hint num">{filtered.length} of {cves.length}</span>
      </div>
      <Table
        columns={columns}
        rows={filtered}
        sort={sort}
        onSort={setSort}
        rowKey={(row) => `${row.cve}|${row.host}|${row.package}`}
        empty="No CVEs match this filter."
      />
    </div>
  );
}

export default CveTable;
