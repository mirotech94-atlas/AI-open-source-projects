// Overview's per-network health strip: one pill per `OverviewPayload.networks`
// entry, each a click-through to Assets → Devices with that network already
// selected.
//
// The pill selects the network *before* navigating, so Assets mounts with the
// chip already on and its first query already scoped — no flash of the whole
// estate followed by a second, narrowed fetch.

import type { ReactNode } from "react";
import { Link, useInRouterContext } from "react-router-dom";
import { EmptyState } from "./EmptyState";
import { NeedsSensorPill } from "./NetworkBadge";
import { atLeast, roleOf, useMe } from "../lib/auth";
import { stateTone } from "../lib/format";
import { setSelectedNetwork } from "../lib/network";
import type { NetworkHealth } from "../types";

export type NetworkHealthStripProps = {
  networks: NetworkHealth[];
};

const ASSETS_DEVICES = "/assets/devices";

export function NetworkHealthStrip({ networks }: NetworkHealthStripProps) {
  const meQuery = useMe();
  const tenantId = meQuery.data?.active_tenant?.id ?? null;
  // `EmptyState`'s own convention: a router hook only runs where a `<Router>`
  // exists, so the check happens before either branch renders rather than at
  // the moment of navigation.
  const inRouter = useInRouterContext();

  if (networks.length === 0) {
    return (
      <EmptyState
        title="No networks yet."
        body="Vault creates your first network from the sensor's first scan. Add more in Settings → Networks."
        action={{ label: "Networks", href: "/settings/networks" }}
      />
    );
  }

  // The empty state already offers a way to add one; before this, the moment
  // a first network existed that offer vanished and the only route to a second
  // was knowing Settings had one. An admin reading this card is exactly the
  // person who wants another network, so the action stays with the list.
  // Viewers and operators do not see it -- `POST /networks` is admin-only and
  // an action that answers 403 is worse than no action.
  const canAdd = atLeast(roleOf(meQuery.data), "admin");

  return (
    <div className="g-overview-networks" data-testid="network-health-strip">
      {networks.map((network) => (
        <NetworkPill
          key={network.id}
          network={network}
          tenantId={tenantId}
          inRouter={inRouter}
        />
      ))}
      {canAdd && inRouter && (
        <Link
          to="/settings/networks?add=1"
          className="g-overview-network-add"
          data-testid="network-add"
        >
          + Add a network
        </Link>
      )}
    </div>
  );
}

function NetworkPill({
  network,
  tenantId,
  inRouter,
}: {
  network: NetworkHealth;
  tenantId: string | null;
  inRouter: boolean;
}) {
  const body = (
    <>
      <span className={`g-dot g-tone-${stateTone(network.state)}`} aria-hidden="true" />
      <span>
        <strong>{network.name}</strong>{" "}
        <span className="g-network-section-counts">
          {network.online}/{network.devices} last reported online
          {network.cameras > 0 ? ` · ${network.cameras_online}/${network.cameras} cameras` : ""}
          {network.crit > 0 ? ` · ${network.crit} critical` : network.warn > 0 ? ` · ${network.warn} warnings` : ""}
        </span>
      </span>
      <NeedsSensorPill status={network.status} />
    </>
  );

  const label =
    `${network.name}, ${network.online} of ${network.devices} devices last reported online` +
    (network.cameras > 0 ? `, ${network.cameras_online} of ${network.cameras} cameras answering` : "") +
    " — show in Assets";

  if (!inRouter) {
    return (
      <span className="g-overview-network-pill" data-testid="network-pill" data-network={network.id}>
        {body}
      </span>
    );
  }

  return (
    <PillLink network={network} tenantId={tenantId} label={label}>
      {body}
    </PillLink>
  );
}

function PillLink({
  network,
  tenantId,
  label,
  children,
}: {
  network: NetworkHealth;
  tenantId: string | null;
  label: string;
  children: ReactNode;
}) {
  return (
    <Link
      to={ASSETS_DEVICES}
      className="g-overview-network-pill"
      data-testid="network-pill"
      data-network={network.id}
      aria-label={label}
      onClick={() => setSelectedNetwork(tenantId, network.id)}
    >
      {children}
    </Link>
  );
}

export default NetworkHealthStrip;
