// The network chooser: an "All networks" chip plus one chip per network,
// each with its name, its online/total device count and a state dot. It sits
// in the Assets sub-header, the Topology toolbar and the Security header, and
// the chip that is on is appended as `?network=<id>` to every scoped query on
// that page.
//
// Not `design/Chips`: that primitive takes a flat `{value, label, count}`
// list and has no room for the state dot, and its single-select semantics
// ("clicking the active chip clears it") are only half of what is needed here
// — "All networks" is a real, nameable choice, not the absence of one.
//
// Renders nothing for a tenant with fewer than two networks. A chooser with
// one option is noise, and it keeps a single-network tenant's UI exactly what
// it was before networks existed. The "needs a sensor" pill is deliberately
// NOT part of this row — it belongs to the thing that needs the sensor (a
// section header, a settings row), so it still shows for a tenant whose only
// network is unreachable.

import { stateTone } from "../lib/format";
import type { NetworkHealth } from "../types";

export type NetworkChipsProps = {
  networks: NetworkHealth[];
  /** `null` = All networks. */
  value: string | null;
  onChange: (next: string | null) => void;
  className?: string;
};

function chipLabel(network: NetworkHealth): string {
  const sensor = network.sensor_ok ? "" : ", no sensor reporting";
  return `${network.name}, ${network.online} of ${network.devices} devices last reported online${sensor}`;
}

export function NetworkChips({ networks, value, onChange, className = "" }: NetworkChipsProps) {
  if (networks.length < 2) return null;

  return (
    <div
      className={`g-chips g-network-chips ${className}`.trim()}
      role="group"
      aria-label="Filter by network"
      data-testid="network-chips"
    >
      <button
        type="button"
        className="g-chip"
        aria-pressed={value === null}
        onClick={() => onChange(null)}
      >
        <span>All networks</span>
      </button>
      {networks.map((network) => (
        <button
          key={network.id}
          type="button"
          className="g-chip"
          data-testid="network-chip"
          data-network={network.id}
          aria-pressed={value === network.id}
          aria-label={chipLabel(network)}
          // Re-clicking the active network falls back to "All networks"
          // rather than being a no-op, so the chooser is always escapable
          // with one click from wherever it is.
          onClick={() => onChange(value === network.id ? null : network.id)}
        >
          <span className={`g-dot g-tone-${stateTone(network.state)}`} aria-hidden="true" />
          <span>{network.name}</span>
          <span className="g-chip-count">
            {network.online}/{network.devices}
          </span>
        </button>
      ))}
    </div>
  );
}

export default NetworkChips;
