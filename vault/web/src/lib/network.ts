// The selected network — the one piece of UI state every network-scoped tab
// shares. `null` means "All networks" (no `?network=` parameter at all), and
// a non-null value is a `Network.id` appended verbatim as `?network=<id>`.
//
// Persisted in `localStorage["guardian.network.<tenantId>"]`, scoped per
// tenant so switching tenants never carries one tenant's network selection
// onto another's (whose ids would not resolve — the hub 404s an unknown
// ref). Every read and write is wrapped in try/catch, same as
// `theme.ts`/`onboarding.ts`: a private window or a browser with site data
// blocked must degrade to "All networks", never throw.
//
// This is the house store pattern from `theme.ts` — a module-level listener
// set plus a per-mount seed from storage — not a new state library. Several
// components read the selection at once (the Assets sub-header chips, the
// Topology toolbar, the Security header, Overview's health pills), so a
// change in one has to reach the others without a reload; and seeding from
// storage on every mount is what keeps tests that clear `localStorage`
// between cases isolated.

import { useCallback, useEffect, useState } from "react";

import type { DeviceCard, DevicesPayload, KindChip } from "../types";

const PREFIX = "guardian.network.";

function storageKey(tenantId: string): string {
  return `${PREFIX}${tenantId}`;
}

const listeners = new Set<(tenantId: string, network: string | null) => void>();

function broadcast(tenantId: string, network: string | null): void {
  for (const listener of listeners) listener(tenantId, network);
}

/** The persisted selection for one tenant, or `null` for "All networks". */
export function readSelectedNetwork(tenantId: string | null): string | null {
  if (tenantId === null) return null;
  try {
    const value = window.localStorage.getItem(storageKey(tenantId));
    return value === null || value === "" ? null : value;
  } catch {
    return null;
  }
}

/** Sets (or clears, with `null`) the selection for one tenant and tells every
 *  mounted `useSelectedNetwork` about it. Exported on its own so a
 *  click-through from another tab — Overview's per-network pill navigating to
 *  Assets — can select the network *and* navigate in the same handler. */
export function setSelectedNetwork(tenantId: string | null, network: string | null): void {
  if (tenantId === null) return;
  try {
    if (network === null) window.localStorage.removeItem(storageKey(tenantId));
    else window.localStorage.setItem(storageKey(tenantId), network);
  } catch {
    // Persistence is a convenience; the in-memory selection still changes.
  }
  broadcast(tenantId, network);
}

/** Forgets one network id wherever it is persisted, whichever tenant holds it.
 *
 *  The per-tenant key above stops one tenant's selection reaching another, but
 *  it cannot help when a network is DELETED inside its own tenant: the id stays
 *  in that tenant's own key, every scoped read 404s, and the page offers a
 *  Retry that can never succeed. The only cure was clearing site data, which is
 *  not something the people this product is for can be asked to do.
 *
 *  Keyed by value rather than by tenant because the one caller that needs this
 *  -- the 404 recovery in `api.request` -- has the dead id and no tenant id. */
export function forgetNetwork(networkId: string): void {
  if (!networkId) return;
  try {
    for (const key of Object.keys(window.localStorage)) {
      if (!key.startsWith(PREFIX)) continue;
      if (window.localStorage.getItem(key) !== networkId) continue;
      window.localStorage.removeItem(key);
      broadcast(key.slice(PREFIX.length), null);
    }
  } catch {
    // Storage unavailable; the in-memory selection is corrected by the
    // broadcast above when it can run, and by the next mount when it cannot.
  }
}

/** The selected network for the active tenant, plus its setter.
 *
 *  `tenantId` is `null` until `useMe()` resolves — the hook then re-seeds
 *  from that tenant's own storage key, so a page mounted before `me` lands
 *  starts on "All networks" and picks up the persisted choice one render
 *  later rather than showing another tenant's network for a tick. */
export function useSelectedNetwork(
  tenantId: string | null,
): [string | null, (next: string | null) => void] {
  const [network, setNetwork] = useState<string | null>(() => readSelectedNetwork(tenantId));

  // Re-seeds when the active tenant becomes known (or changes). This
  // genuinely synchronizes with an external, network-backed value rather
  // than deriving something render already has — the case the
  // set-state-in-effect rule carves out, same as `Onboarding.tsx`'s resume.
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    setNetwork(readSelectedNetwork(tenantId));
  }, [tenantId]);

  useEffect(() => {
    function onChange(changedTenant: string, next: string | null) {
      if (changedTenant === tenantId) setNetwork(next);
    }
    listeners.add(onChange);
    return () => {
      listeners.delete(onChange);
    };
  }, [tenantId]);

  const select = useCallback(
    (next: string | null) => {
      setNetwork(next);
      setSelectedNetwork(tenantId, next);
    },
    [tenantId],
  );

  return [network, select];
}

/** The `?network=` query fragment for a scoped read, or `""` for "All
 *  networks". One place builds it, so no caller can drift into sending
 *  `?network=all` (which the hub treats as unscoped anyway) or an
 *  unencoded id. */
export function networkQuery(network?: string | null): string {
  return network === null || network === undefined || network === ""
    ? ""
    : `?network=${encodeURIComponent(network)}`;
}

/* ------------------------------------------- network-derived helpers --- */

/** A network is only carrying real data once it has a sensor that can reach
 *  it. `pending` is the moment between "added in Settings" and the first
 *  push, `no-sensor` and `unreachable` are the two states an operator has to
 *  act on — the hub's own `Network.status` vocabulary (task 51 §2). */
export function needsSensor(status: string): boolean {
  return status === "no-sensor" || status === "unreachable";
}

/** Kind chips for one network. The hub tags each chip with the network it
 *  counted (`KindChip.network_id`); when it sends untagged chips — a tenant
 *  whose rows predate the backfill — they are re-derived from the devices in
 *  this section, so the chip counts can never disagree with the grid below
 *  them. */
function chipsFor(payload: DevicesPayload, networkId: string, devices: DevicesPayload["groups"]): KindChip[] {
  const tagged = payload.chips.filter((chip) => chip.network_id === networkId);
  if (tagged.length > 0) return tagged;
  return devices.map((group) => ({
    group: group.group,
    count: group.devices.length,
    online: group.devices.filter((d) => d.online).length,
    network_id: networkId,
  }));
}

/** The devices payload narrowed to one network. A device the hub could not
 *  attribute (`network: null`) belongs to no section — `Assets` renders those
 *  in their own trailing block instead of quietly filing them under the first
 *  network. */
export function sliceByNetwork(payload: DevicesPayload, networkId: string): DevicesPayload {
  const groups = payload.groups
    .map((group) => ({ ...group, devices: group.devices.filter((d) => d.network?.id === networkId) }))
    .filter((group) => group.devices.length > 0);
  const devices = groups.flatMap((g) => g.devices);
  return {
    ...payload,
    groups,
    chips: chipsFor(payload, networkId, groups),
    total: devices.length,
    online: devices.filter((d) => d.online).length,
    unverified: devices.filter((d) => d.unverified).length,
  };
}


/** Free-text device search — the reference console's `#devq` box, which
 *  matches on name, IP, MAC, vendor and the owner's own label. Filtering is
 *  local to the payload already fetched: it never re-reads, and it never
 *  touches the summary tiles above it, which describe the network rather than
 *  the search.
 *
 *  Chips are recounted from what survives, so a chip can never claim more
 *  devices than the grid under it draws. An empty query returns the payload
 *  untouched (same object), so the common case allocates nothing. */
/** Which devices the Assets grid is showing.
 *
 *  `unidentified` is one of them because on a security product it is the
 *  most important thing in the list: a device Vault cannot name is the
 *  one that got onto the network without anybody deciding it should. */
export type DeviceLens = "all" | "online" | "away" | "unidentified";

function matchesLens(d: DeviceCard, lens: DeviceLens): boolean {
  if (lens === "online") return d.online;
  if (lens === "away") return !d.online;
  if (lens === "unidentified") return d.unverified || !(d.label || d.name);
  return true;
}

export function searchDevices(
  payload: DevicesPayload, query: string, lens: DeviceLens = "all",
): DevicesPayload {
  const needle = query.trim().toLowerCase();
  if (needle === "" && lens === "all") return payload;
  const groups = payload.groups
    .map((group) => ({
      ...group,
      devices: group.devices.filter(
        (d) => matchesLens(d, lens)
          && (needle === ""
              || [d.name, d.label, d.ip, d.mac, d.vendor].some((f) => f.toLowerCase().includes(needle))),
      ),
    }))
    .filter((group) => group.devices.length > 0);
  const devices = groups.flatMap((g) => g.devices);
  return {
    ...payload,
    groups,
    chips: payload.chips
      .map((chip) => {
        const matched = devices.filter(
          (d) => d.group === chip.group && (chip.network_id === null || d.network?.id === chip.network_id),
        );
        return { ...chip, count: matched.length, online: matched.filter((d) => d.online).length };
      })
      .filter((chip) => chip.count > 0),
    total: devices.length,
    online: devices.filter((d) => d.online).length,
    unverified: devices.filter((d) => d.unverified).length,
  };
}

