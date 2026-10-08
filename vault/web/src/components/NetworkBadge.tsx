// The small inline "which network is this on?" chip, drawn on a device card,
// an inbox row, a DNS client, an attacker row and the topology hover card.
//
// Renders nothing when `network` is `null`: a row the hub could not attribute
// to a network (or a tenant that has only ever had one) must look exactly the
// way it did before networks existed, not gain an empty grey pill.

import { needsSensor } from "../lib/network";
import type { NetworkRef } from "../types";

export type NetworkBadgeProps = {
  network: NetworkRef | null;
  className?: string;
};

export function NetworkBadge({ network, className = "" }: NetworkBadgeProps) {
  if (network === null) return null;
  return (
    <span
      className={`g-network-badge ${className}`.trim()}
      data-testid="network-badge"
      data-network={network.id}
      title={`On the ${network.name} network`}
    >
      {network.name}
    </span>
  );
}

/** The one piece of copy that tells an operator what to do about a network
 *  with no working sensor. Deliberately the same sentence everywhere it
 *  appears (the Assets section header, the Settings → Networks table) so a
 *  support answer can quote it verbatim. */
export function NeedsSensorPill({ status }: { status: string }) {
  if (!needsSensor(status)) return null;
  return (
    <span className="g-network-needs-sensor g-tone-warn" data-testid="needs-sensor">
      needs a sensor on this network
    </span>
  );
}

export default NetworkBadge;
