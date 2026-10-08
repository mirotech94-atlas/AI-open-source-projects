// One network's slice of the Devices sub-view: a header (name, chip counts,
// the "needs a sensor on this network" pill) and, inside it, the ordinary
// kind-chip grid from Task 26 — so the kind chips filter *within* the
// network rather than across the whole estate.
//
// The slicing lives here rather than in `DeviceGrid` so the grid stays the
// one component that knows how to draw devices, and the section stays the one
// that knows about networks.
//
// The network-map direction's one carry-over into Assets: a camera this
// network's recorder claims but Vault has never listed (`RecorderGap`,
// scoped by its own `network_id`) renders as a dashed, unfilled card
// appended into the SAME `.g-asset-grid` DeviceGrid draws into — never a
// separate box, never invented when a recorder has no gap here.

import { DeviceGrid } from "./DeviceGrid";
import { NeedsSensorPill } from "./NetworkBadge";
import { sliceByNetwork } from "../lib/network";
import type { DevicesPayload, NetworkHealth, RecorderGap } from "../types";

export type NetworkSectionProps = {
  network: NetworkHealth;
  payload: DevicesPayload;
  selectedKind: string | null;
  onSelectKind: (kind: string | null) => void;
  onOpen: (mac: string) => void;
  /** Every camera-census gap the overview reported (Task 83) — filtered to
   *  this network's own id here, same pattern as `sliceByNetwork` above.
   *  Defaults to none so a caller that has not fetched the census yet (or a
   *  test fixture) never has to pass it. */
  cameraGaps?: RecorderGap[];
  /** Opens the explanation drawer for one unconfirmed camera slot. Required
   *  only when `cameraGaps` might be non-empty. */
  onOpenGap?: (gap: RecorderGap) => void;
};

export function NetworkSection({
  network,
  payload,
  selectedKind,
  onSelectKind,
  onOpen,
  cameraGaps = [],
  onOpenGap,
}: NetworkSectionProps) {
  const scoped = sliceByNetwork(payload, network.id);
  const gaps = cameraGaps.filter((g) => g.network_id !== "" && g.network_id === network.id);
  const missing = gaps.reduce((sum, g) => sum + Math.max(0, g.expected - g.known), 0);
  // Cameras only ever show up mixed into the grid when every kind is shown,
  // or when the kind filter is narrowed to cameras themselves — matching
  // exactly which grid the real camera cards are currently rendered into.
  const showGhosts = missing > 0 && (selectedKind === null || selectedKind === "camera");

  return (
    <section className="g-network-section" data-testid="network-section" data-network={network.id}>
      <div className="g-network-section-head">
        <h3>{network.name}</h3>
        <span className="g-network-section-counts">
          {network.online}/{network.devices} last reported online
          {network.cameras > 0 ? ` · ${network.cameras_online}/${network.cameras} cameras answering` : ""}
          {missing > 0 ? ` · ${missing} never listed` : ""}
          {network.unverified > 0 ? ` · ${network.unverified} unverified` : ""}
        </span>
        <NeedsSensorPill status={network.status} />
      </div>
      <DeviceGrid
        payload={scoped}
        selectedKind={selectedKind}
        onSelectKind={onSelectKind}
        onOpen={onOpen}
        showNetwork={false}
        chipsLabel={`Filter ${network.name} by device kind`}
      />
      {showGhosts && (
        <div className="g-asset-grid g-asset-grid-ghosts" role="list" aria-label={`Cameras ${network.name} has never listed`}>
          {gaps.flatMap((gap, gi) =>
            Array.from({ length: Math.max(0, gap.expected - gap.known) }, (_, si) => (
              <div
                key={`${gap.network_id}-${gi}-${si}`}
                data-testid="camera-gap-card"
                role="button"
                tabIndex={0}
                className="g-asset-card g-asset-card-ghost"
                aria-label={`Camera the recorder claims but Vault has never listed, behind ${gap.name}`}
                onClick={() => onOpenGap?.(gap)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onOpenGap?.(gap);
                  }
                }}
              >
                <div className="g-asset-card-head">
                  <span className="g-device-name">Not listed</span>
                </div>
                <p className="g-asset-card-url">{gap.name}</p>
                <p className="hint">{gap.reason}</p>
              </div>
            )),
          )}
        </div>
      )}
    </section>
  );
}

export default NetworkSection;
