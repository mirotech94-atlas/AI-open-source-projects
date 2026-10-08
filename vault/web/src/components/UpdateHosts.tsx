import { useState } from "react";
import { Badge, Table } from "../design";
import type { Column, SortState } from "../design";
import { ago } from "../lib/format";
import type { UpdateHost } from "../types";

export type UpdateHostsProps = {
  hosts: UpdateHost[];
  /** Start a repair on one host. The page owns what happens next — a
   *  reversible action runs, a disruptive one asks first. Omitted (with
   *  `canWrite` false) leaves the table read-only. */
  onFix?: (host: UpdateHost, action: string) => void;
  canWrite?: boolean;
  writeTooltip?: string;
  /** Host names with a repair already in flight, so a button cannot be
   *  pressed twice while the sensor is still running the first one. */
  busy?: string[];
};

/** The reference console's own kernel readout: the running kernel, and the
 *  newer one waiting for a reboot named beside it. A host whose newest
 *  installed kernel is the one it booted says nothing extra. */
function kernelCell(host: UpdateHost) {
  const stale = host.kernel_latest !== "" && host.kernel_latest !== host.kernel;
  return (
    <span className="num" title={stale ? `${host.kernel_latest} is installed and waiting for a reboot` : undefined}>
      {host.kernel || "—"}
      {stale && <span className="hint"> → {host.kernel_latest}</span>}
    </span>
  );
}

/**
 * The per-server update table: the reference's `#updtbl`, column for column —
 * Host · OS · Kernel · Pending · Security · Auto-updates · Last apt run ·
 * Reboot · Actions. Every value comes from `UpdatesPayload.hosts`.
 *
 * The Actions column carries the reference's two apt buttons. "Security now"
 * is a reversible repair and runs on one press; "Upgrade all" is disruptive,
 * so it goes through the hub's tier-3 confirmation (see `Updates.tsx`) and can
 * never run on a bare click. Reboots stay with the Reboot planner further down
 * the tab, which is window-aware in a way a bare button is not.
 */
export function UpdateHosts({
  hosts,
  onFix,
  canWrite = false,
  writeTooltip = "admin role required",
  busy = [],
}: UpdateHostsProps) {
  const [sort, setSort] = useState<SortState | null>(null);

  const columns: Column<UpdateHost>[] = [
    {
      key: "name",
      header: "Server",
      render: (row) => (
        <span className="num">
          {row.name}
          {/* Item 30: a server the sweep never reached used to be absent
              from this table altogether, and absent reads as fine. It is
              listed now, and says why its numbers are blank -- zero
              pending because Vault has no answer, not because there is
              nothing to install. */}
          {!row.checked && <Badge tone="warn">not checked</Badge>}
        </span>
      ),
    },
    { key: "os", header: "OS", render: (row) => (row.checked ? row.os || "—" : "—") },
    { key: "kernel", header: "Kernel", render: kernelCell },
    {
      key: "pending",
      header: "Pending",
      align: "right",
      render: (row) => (row.checked ? <span className="num">{row.pending}</span> : <span className="hint">unknown</span>),
    },
    {
      key: "security_pending",
      header: "Security",
      align: "right",
      render: (row) =>
        !row.checked ? (
          <span className="hint">unknown</span>
        ) : row.security_pending === 0 ? (
          <span className="num hint">0</span>
        ) : (
          <span className="num g-tone-alert">{row.security_pending}</span>
        ),
    },
    {
      key: "cve_fixable",
      header: "Fixable CVEs",
      align: "right",
      render: (row) =>
        row.cve_fixable === 0 ? (
          <span className="num hint">0</span>
        ) : (
          <span className="num g-tone-warn">{row.cve_fixable}</span>
        ),
    },
    {
      key: "unattended",
      header: "Auto-updates",
      align: "center",
      // `null` is "the sensor never reported this", which is not the same
      // claim as "off" — an em dash, never a red "off".
      render: (row) =>
        row.unattended === null ? (
          <span className="hint">—</span>
        ) : (
          <Badge tone={row.unattended ? "good" : "warn"}>{row.unattended ? "on" : "off"}</Badge>
        ),
    },
    {
      key: "last_apt",
      header: "Last apt run",
      render: (row) => <span className="num hint">{row.last_apt === "" ? "—" : ago(row.last_apt)}</span>,
    },
    {
      key: "reboot_required",
      header: "Reboot",
      align: "center",
      render: (row) => (row.reboot_required ? <Badge tone="warn">required</Badge> : <span className="hint">—</span>),
    },
  ];

  if (onFix !== undefined) {
    columns.push({
      key: "actions",
      header: "Actions",
      render: (row) => {
        const running = busy.includes(row.name);
        const title = canWrite ? undefined : writeTooltip;
        return (
          <span className="g-updates-actions">
            {row.security_packages.length > 0 && (
              <button
                type="button"
                disabled={!canWrite || running}
                title={title}
                onClick={() => onFix(row, "security_updates")}
              >
                Security now
              </button>
            )}
            {row.pending > 0 && (
              <button
                type="button"
                disabled={!canWrite || running}
                title={title}
                onClick={() => onFix(row, "full_upgrade")}
              >
                Upgrade all…
              </button>
            )}
            {!row.checked && (
              <span className="hint">Vault could not reach this server on the last sweep.</span>
            )}
            {row.checked && row.security_packages.length === 0 && row.pending === 0 && (
              <span className="hint">nothing pending</span>
            )}
          </span>
        );
      },
    });
  }

  return (
    <Table
      columns={columns}
      rows={hosts}
      sort={sort}
      onSort={setSort}
      rowKey={(row) => row.name}
      empty="No servers are being watched yet."
    />
  );
}

export default UpdateHosts;
