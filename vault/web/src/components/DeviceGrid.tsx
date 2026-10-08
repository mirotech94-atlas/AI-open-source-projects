import type { HTMLAttributes } from "react";
import { machineClass } from "../lib/machine";
import { Badge, Chips } from "../design";
import { EmptyState } from "./EmptyState";
import { NetworkBadge } from "./NetworkBadge";
import { ago } from "../lib/format";
import type { DeviceCard, DevicesPayload } from "../types";

export type DeviceGridProps = {
  payload: DevicesPayload;
  selectedKind: string | null;
  onSelectKind: (kind: string | null) => void;
  onOpen: (mac: string) => void;
  /** Draws each card's network badge. Off inside a per-network section
   *  (`NetworkSection`), where the heading above already says which network
   *  every card in it is on. */
  showNetwork?: boolean;
  /** Accessible name for the kind-chip group. Assets draws one grid per
   *  network, so the default cannot stay: two groups with the identical name
   *  "Filter by device kind" leave a screen-reader or voice-control user with
   *  no way to say which network's filter they mean. `NetworkSection` passes
   *  the network's own name in. */
  chipsLabel?: string;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children">;

/** Display labels for the kind chips, per spec §8. A group the hub sends
 *  that this build predates still renders — title-cased with underscores
 *  turned into spaces — rather than showing nothing. */
const KIND_LABELS: Record<string, string> = {
  camera: "Cameras",
  cameras: "Cameras",
  phone: "Phones & tablets",
  phones: "Phones & tablets",
  computer: "Computers",
  computers: "Computers",
  tv: "TVs & media",
  media: "TVs & media",
  smart_home: "Smart home",
  network: "Network gear",
  unknown: "Unknown",
};

function kindLabel(group: string): string {
  return KIND_LABELS[group] ?? group.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

function riskTone(risk: number): "plain" | "good" | "warn" | "alert" {
  if (risk >= 70) return "alert";
  if (risk >= 40) return "warn";
  return "good";
}

function trustBadge(device: DeviceCard): { label: string; tone: "good" | "warn" | "alert" | "plain" } {
  if (device.quarantined) return { label: "Quarantined", tone: "alert" };
  if (device.trusted) return { label: "Trusted", tone: "good" };
  if (device.unverified) return { label: "Unverified", tone: "warn" };
  return { label: "Untrusted", tone: "plain" };
}

export function DeviceGrid({
  payload,
  selectedKind,
  onSelectKind,
  onOpen,
  showNetwork = true,
  chipsLabel = "Filter by device kind",
  className = "",
  ...rest
}: DeviceGridProps) {
  const devices = payload.groups.flatMap((g) => g.devices);
  const filtered = selectedKind === null ? devices : devices.filter((d) => d.group === selectedKind);

  return (
    <div className={`g-asset-devices ${className}`.trim()} {...rest}>
      <Chips
        label={chipsLabel}
        items={payload.chips.map((chip) => ({
          value: chip.group,
          label: kindLabel(chip.group),
          count: chip.count,
        }))}
        value={selectedKind}
        onChange={onSelectKind}
      />

      {filtered.length === 0 ? (
        devices.length === 0 ? (
          // Nobody has ever appeared on the network, not just this kind — the
          // sensor is likely still not installed, so point straight at the
          // onboarding wizard rather than the generic per-kind message.
          <EmptyState
            title="No devices seen yet."
            body="Install the sensor and Vault will start listing every device it finds on your network here."
            action={{ label: "Install the sensor", href: "/welcome" }}
          />
        ) : (
          <p className="g-asset-empty">No devices in this kind yet.</p>
        )
      ) : (
        <div className="g-asset-grid">
          {filtered.map((device) => {
            const trust = trustBadge(device);
            return (
              <div
                key={device.mac}
                role="button"
                tabIndex={0}
                data-testid="device-card"
                className="g-asset-card"
                onClick={() => onOpen(device.mac)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onOpen(device.mac);
                  }
                }}
              >
                <div className="g-asset-card-head">
                  <span className="g-device-name">
                    <span
                      className={`g-dot g-tone-${device.online ? "good" : "plain"}`}
                      aria-hidden="true"
                    />
                    {/* Falls back through vendor to the MAC, matching
                        `DevicesTable` -- a device with no hostname read as a
                        maker's name in the table and as raw hex on its card.
                        A MAC is then read character by character; see
                        `lib/machine`, where the decision is per value because
                        the same span holds "Miro's iPhone" on the next row. */}
                    {(() => { const v = device.label || device.name || device.vendor || device.mac;
                              return <span className={machineClass(v)}>{v}</span>; })()}
                  </span>
                  {/* Item 15: where that name came from. "staff-phone" is a
                      name you gave it; "Espressif" is only a maker; a bare
                      MAC is a device nobody has identified -- and on a
                      security product that last one is the row to look at,
                      so it should not be silently indistinguishable from
                      the first. */}
                  <Badge tone={device.label || device.name ? "plain" : device.vendor ? "plain" : "warn"}>
                    {device.label ? "named" : device.name ? "hostname" : device.vendor ? "maker only" : "unidentified"}
                  </Badge>
                  <Badge tone={riskTone(device.risk)}>risk {device.risk}</Badge>
                </div>
                <p className="g-asset-card-url">{device.ip || device.mac}</p>
                <div className="g-asset-card-foot">
                  <Badge tone={trust.tone}>{trust.label}</Badge>
                  {device.always_on && <Badge tone="plain">Always on</Badge>}
                  {showNetwork && <NetworkBadge network={device.network} />}
                  <span className="g-device-seen">{device.online ? "online" : `${device.presence === "UNKNOWN" ? "unknown · " : ""}seen ${ago(device.last_seen)}`}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default DeviceGrid;
