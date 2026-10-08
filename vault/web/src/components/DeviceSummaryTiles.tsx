// The nine tiles the Devices view opens with, in the reference console's own
// order and with its own definitions (`#netkpis`): four about the devices
// themselves, then the internet the network reaches them over.
//
// Every value comes from `DevicesPayload` — nothing is derived from a second
// source and nothing is aggregated here, so a tile and the grid below it can
// never disagree. The row is keyed on the same query as the grid, so it follows
// the network chooser without any wiring of its own.
//
// Read-only by design: these are figures, not filters. The kind chips below are
// the filter, and giving a tile a second, overlapping filtering behaviour would
// leave two controls fighting over one selection.

import { Stat } from "../design";
import type { Tone } from "../lib/format";
import type { DevicesPayload } from "../types";

export type DeviceSummaryTilesProps = {
  payload: DevicesPayload;
};

/** A number, or the em dash the whole app uses for "not measured". Never 0 —
 *  `0 Mbps` is a claim about the link, and the hub sends `null` when there is
 *  no sample at all. */
function num(value: number | null, digits = 1): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(digits);
}

type Tile = {
  label: string;
  value: string;
  /** The smaller-weight text beside the number: the unit, or the denominator. */
  unit?: string;
  tone: Tone;
  title?: string;
  /** Draw the value smaller. An IPv4 address is 15 characters and does not fit
   *  a 140 px tile at the 21 px stat size — the reference drops the WAN tile to
   *  14 px for exactly this reason, rather than letting it ellipsise. */
  compact?: boolean;
};

/** The WAN tile's value.
 *
 *  A network's public address is its sensor's egress, so a tenant watching two
 *  networks through one sensor has exactly one — and the tile names it. A
 *  tenant whose networks each have their own sensor genuinely has two, and
 *  under "All networks" there is no single address to print: the tile shows how
 *  many there are and lists them in its tooltip. Picking one of them would be
 *  inventing a fact the data does not support. */
function wanTile(payload: DevicesPayload): Tile {
  const { wan_ip: ip, wan_ips: ips } = payload.tiles;
  if (ips.length > 1) {
    return {
      label: "WAN IP",
      value: String(ips.length),
      unit: "addresses",
      tone: "plain",
      title: ips.join(", "),
    };
  }
  return { label: "WAN IP", value: ip || "—", tone: "plain", title: ip || undefined, compact: ip !== "" };
}

function tilesOf(payload: DevicesPayload): Tile[] {
  const t = payload.tiles;
  return [
    {
      label: "Devices online",
      value: String(payload.online),
      unit: `/ ${payload.total} known`,
      tone: "plain",
    },
    {
      // The only tile the reference tones from its own value: something new on
      // the network is worth a look, and nothing new is worth saying so.
      label: "New this scan",
      value: String(t.new_devices),
      tone: t.new_devices > 0 ? "warn" : "good",
    },
    { label: "Trusted", value: String(t.trusted), tone: "plain" },
    { label: "Randomized MACs", value: String(t.randomized), tone: "plain" },
    { label: "Download", value: num(t.down_mbps), unit: "Mbps", tone: "plain" },
    { label: "Upload", value: num(t.up_mbps), unit: "Mbps", tone: "plain" },
    { label: "Latency", value: num(t.latency_ms), unit: "ms", tone: "plain" },
    {
      label: "DNS (system)",
      // A resolver that answered shows its timing; one that failed says so, and
      // one that was never sampled is a dash rather than a failure.
      value: t.dns_ok === false ? "FAIL" : num(t.dns_ms, 0),
      unit: t.dns_ok === false || t.dns_ms === null ? undefined : "ms",
      tone: t.dns_ok === false ? "alert" : "plain",
    },
    wanTile(payload),
  ];
}

export function DeviceSummaryTiles({ payload }: DeviceSummaryTilesProps) {
  return (
    <div className="g-device-tiles" data-testid="device-tiles">
      {tilesOf(payload).map((tile) => (
        <div
          key={tile.label}
          className={`g-card g-tone-${tile.tone}${tile.compact === true ? " g-device-tile-compact" : ""}`}
          title={tile.title}
        >
          <Stat label={tile.label} value={tile.value} unit={tile.unit} tone={tile.tone} mono />
        </div>
      ))}
    </div>
  );
}

export default DeviceSummaryTiles;
