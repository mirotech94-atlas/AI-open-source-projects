import { Badge } from "../design";
import { EmptyState } from "./EmptyState";
import { ago } from "../lib/format";
import type { MacUpdates, UpdateHost } from "../types";

/** The single most useful fact on this tab, said once and said first: which
 *  hosts have a patch sitting installed-but-inactive because nothing has
 *  restarted them yet. `reboot_required` is the hub's own signal; the kernel
 *  pair (`kernel` vs `kernel_latest`) is what turns "a restart is needed"
 *  into "this specific fix is not live until it happens" when the hub has
 *  that detail, and a plain sentence when it does not. */
export function RestartNeeded({ hosts }: { hosts: UpdateHost[] }) {
  const needing = hosts.filter((h) => h.reboot_required);
  if (needing.length === 0) {
    return <p className="g-asset-empty">No host is waiting on a restart. Every applied patch is already active.</p>;
  }
  return (
    <ul className="g-restart-list">
      {needing.map((host) => {
        const staleKernel = host.kernel_latest !== "" && host.kernel_latest !== host.kernel;
        return (
          <li key={host.name} className="g-restart-row">
            <span className="num g-restart-host">{host.name}</span>
            <span className="g-restart-note">
              {staleKernel ? (
                <>
                  Kernel <span className="num">{host.kernel_latest}</span> is installed and waiting — the fix is
                  not active until this host restarts.
                </>
              ) : (
                "A restart is needed to finish applying an update already installed."
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * "Pending packages by host" and "This Mac" — the reference console's two
 * panels under the servers table (`#updlist` and `#macupd`).
 *
 * Both read fields the hub already had in `Scan.raw` and never sent: the
 * per-host package list and the sensor host's own update block. Every package
 * name is in the monospace numeric face; a name apt marks as a security update
 * is toned, which is the reference's own distinction.
 */
export function PendingPackages({ hosts }: { hosts: UpdateHost[] }) {
  const waiting = hosts.filter((h) => h.pending > 0 || h.packages.length > 0);
  if (waiting.length === 0) {
    return <EmptyState title="Nothing pending." body="Every watched server has installed everything apt offers it." />;
  }
  return (
    <ul className="g-pkglist">
      {waiting.map((host) => {
        const security = new Set(host.security_packages);
        const hidden = Math.max(0, host.pending - host.packages.length);
        return (
          <li key={host.name}>
            <p className="g-pkglist-host">
              <strong className="num">{host.name}</strong>{" "}
              <span className="hint num">
                {host.pending} {host.pending === 1 ? "package" : "packages"}
              </span>
            </p>
            {host.packages.length === 0 ? (
              <p className="hint">The sensor has not listed them yet.</p>
            ) : (
              <p className="g-pkglist-names">
                {host.packages.map((name) => (
                  <span key={name} className={security.has(name) ? "num g-tone-warn" : "num hint"}>
                    {name}
                  </span>
                ))}
                {hidden > 0 && <span className="hint">and {hidden} more</span>}
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** The machine Vault's sensor runs on. It patches itself through the OS,
 *  which is why this panel says what is waiting and who has to install it. */
export function SensorHostUpdates({ mac }: { mac: MacUpdates }) {
  if (!mac.reported) {
    return (
      <EmptyState
        title="The sensor has not reported its own updates yet."
        body="It checks its own machine on a slower cycle than it scans the network."
      />
    );
  }
  return (
    <dl className="g-facts">
      <dt>Operating system</dt>
      <dd className="num">{mac.os || "—"}</dd>
      <dt>Available</dt>
      <dd>
        {mac.error !== "" ? (
          <>
            <Badge tone="warn">check failed</Badge> <span className="hint">{mac.error}</span>
          </>
        ) : mac.available.length === 0 ? (
          <Badge tone="good">up to date</Badge>
        ) : (
          <span className="g-pkglist-names">
            {mac.available.map((name) => (
              <span key={name} className="num">
                {name}
              </span>
            ))}
          </span>
        )}
      </dd>
      <dt>Checked</dt>
      <dd className="num hint">{mac.checked === "" ? "—" : ago(mac.checked)}</dd>
      <dt>How</dt>
      <dd className="hint">
        Install these on the machine itself — Vault never applies an operating-system update to its own sensor
        host.
      </dd>
    </dl>
  );
}

export default PendingPackages;
