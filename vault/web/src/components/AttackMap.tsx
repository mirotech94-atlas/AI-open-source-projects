import { useMemo, useState } from "react";
import { geoNaturalEarth1, geoPath } from "d3-geo";
import type { GeoPermissibleObjects } from "d3-geo";
import { Drawer } from "../design";
import { NetworkBadge } from "./NetworkBadge";
import { ago, day } from "../lib/format";
import { WORLD } from "../assets/world-110m";
import type { Attacker } from "../types";

export type AttackMapProps = {
  attackers: Attacker[];
  /** Fired when "Block this /24" is pressed for the attacker open in the
   *  drawer. The caller owns the write (`POST /api/v1/fix
   *  {action:"block_subnet", ip}`) — this component only reports which
   *  attacker was chosen. */
  onBlock: (attacker: Attacker) => void;
  canWrite?: boolean;
  writeTooltip?: string;
  /** Attacker IPs with a block currently queued: the drawer's button for that
   *  attacker shows "Blocking…" and disables rather than double-submitting —
   *  same convention as `ScorecardGrid`'s `pending`. */
  pending?: Set<string>;
  /** `payload.attacker_method` — the policy Vault currently applies to a
   *  NEW attacker. "off" means nothing is handled automatically; anything
   *  else means a repeat offender gets the same treatment again without a
   *  person doing anything. This is a forward-looking policy statement, not
   *  a property of any one attacker, so it lives in the legend rather than
   *  on a row. */
  attackerMethod?: string;
};

// A fixed intrinsic size for the projection + viewBox. The SVG itself scales
// to its container's width (see `.g-sec-attackmap-svg` — `width: 100%`,
// `height: auto`), so this is only the coordinate space markers are placed
// in, not a pixel size on screen.
const VIEW_W = 960;
const VIEW_H = 500;

function radiusPx(fails: number): number {
  return Math.min(18, Math.max(6, 6 + Math.sqrt(Math.max(0, fails))));
}

/** One attacker per ADDRESS. The hub's `security.attackers()` keys its rows
 *  by (host, ip), so one address that hit two hosts arrives twice -- and
 *  used to be counted twice by the toggle, drawn twice on the same spot
 *  (one visible dot), and keyed twice in React. A block is per address
 *  (`Fix.arg == ip`), so the address is the unit here; the hosts it hit
 *  are kept for the drawer. */
type MapAttacker = Attacker & { hosts: string[] };

function mergeByIp(attackers: Attacker[]): MapAttacker[] {
  const byIp = new Map<string, MapAttacker>();
  for (const a of attackers) {
    const seen = byIp.get(a.ip);
    if (seen === undefined) {
      byIp.set(a.ip, { ...a, hosts: a.host ? [a.host] : [] });
      continue;
    }
    if (a.host && !seen.hosts.includes(a.host)) seen.hosts.push(a.host);
    seen.fails += a.fails;
    seen.days = Math.max(seen.days, a.days);
    if (a.first_seen && (!seen.first_seen || a.first_seen < seen.first_seen)) seen.first_seen = a.first_seen;
    if (a.last_seen > seen.last_seen) seen.last_seen = a.last_seen;
    if (seen.geo === null) seen.geo = a.geo;
    if (seen.action === "" && a.action !== "") {
      seen.action = a.action;
      seen.action_ts = a.action_ts;
    }
    if (seen.network === null) seen.network = a.network;
  }
  return [...byIp.values()];
}

type Marker = { attackers: MapAttacker[]; xPct: number; yPct: number };

function hasCoords<T extends Attacker>(a: T): a is T & { geo: NonNullable<Attacker["geo"]> } {
  return a.geo !== null && a.geo.lat !== null && a.geo.lon !== null;
}

/** True once a real, permanent firewall rule exists for this address — the
 *  one fact `security.attackers()` actually carries (`Fix.arg == ip`, most
 *  recent). There is no field for "still in force" beyond that: the rule is
 *  a host firewall entry Vault does not revert on its own, so a non-empty
 *  `action` IS "handled", not merely "was handled once". */
function isHandled(a: Attacker): boolean {
  return a.action !== "";
}

// `Attacker.action` is one of `METHODS` (hub/guardian_hub/payloads/
// security.py) minus "auto"/"off" — the concrete action a Fix row recorded,
// not the policy name. A plain word for each, for the drawer and the list;
// anything unrecognised (a future action this UI hasn't been taught yet)
// falls back to the raw value rather than hiding it.
const ACTION_WORDS: Record<string, string> = {
  block_ip: "blocked",
  block_subnet: "blocked (whole /24)",
  ban_long: "banned long-term",
  limit_ssh: "SSH rate-limited",
};

function actionWord(action: string): string {
  return ACTION_WORDS[action] ?? action;
}

/** The sentence a marker or list row states about itself — never a colour
 *  alone. `action_ts` is the one timestamp the payload actually carries for
 *  a block, so a handled attacker names it; when the payload has no
 *  timestamp for some reason this still says the action happened, just not
 *  when. */
function statusSentence(a: Attacker): string {
  if (isHandled(a)) {
    const when = a.action_ts ? day(a.action_ts) : null;
    return when
      ? `Blocked automatically, ${when}.`
      : `Blocked automatically (${actionWord(a.action)}).`;
  }
  return "Still active — needs your attention.";
}

/** Attackers Vault could not place on the map are never dropped from the
 *  count, but the payload gives no reason code for *why* a given IP has no
 *  `geo` (private range, a provider miss, or just not reached yet this
 *  request under the per-load lookup budget) — so this says only what is
 *  actually known, instead of guessing which one applied. */
const UNLOCATED_REASON =
  "Vault could not get a location for this address from its geolocation lookup. " +
  "This can happen for an address that has not been looked up yet, or when the lookup " +
  "provider has no answer for it — it is retried on a later refresh.";

/** `d3-geo`'s Natural Earth projection over the bundled 110m world outline.
 *  A marker states its own status in words (in its accessible name and in
 *  the list beside the map) — colour is a hint, never the only signal, and
 *  a handled attacker never reads as "good": red marks what still needs
 *  attention, a neutral/checked tone marks what Vault already blocked.
 *  Handled attackers are hidden by default (the owner's own request — "I
 *  checked, don't show me the ones you've already dealt with") but are
 *  never unreachable: a count and a one-click toggle bring them back,
 *  because the record that Vault blocked one is the evidence it did the
 *  work. Clicking a marker or a list row opens a drawer with a "Block this
 *  /24" button. */
export function AttackMap({
  attackers,
  onBlock,
  canWrite = true,
  writeTooltip = "admin role required",
  pending = new Set(),
  attackerMethod = "",
}: AttackMapProps) {
  const [selected, setSelected] = useState<MapAttacker | null>(null);
  const [showHandled, setShowHandled] = useState(false);

  const merged = useMemo(() => mergeByIp(attackers), [attackers]);

  const projection = useMemo(
    () => geoNaturalEarth1().fitSize([VIEW_W, VIEW_H], WORLD as unknown as GeoPermissibleObjects),
    [],
  );
  const path = useMemo(() => geoPath(projection), [projection]);

  const handledCount = useMemo(() => merged.filter(isHandled).length, [merged]);
  // A revealed attacker with no geolocation is in the count and the list but
  // never on the map -- said beside the toggle, or "5 hidden" reveals 3 dots
  // and reads as a lie.
  const handledUnlocated = useMemo(
    () => merged.filter((a) => isHandled(a) && !hasCoords(a)).length,
    [merged],
  );
  // Default view: what still needs attention. `showHandled` adds the rest
  // back in rather than ever making them unreachable.
  const visible = useMemo(
    () => (showHandled ? merged : merged.filter((a) => !isHandled(a))),
    [merged, showHandled],
  );

  const located = useMemo(() => visible.filter(hasCoords), [visible]);
  const unlocated = useMemo(() => visible.filter((a) => !hasCoords(a)), [visible]);

  // One pin per PLACE, not per attacker. Two IPs from the same city (a
  // scanner's /24 neighbours, geolocated to one point) project onto the same
  // pixel and read as a single attacker -- "5 handled" with three dots.
  // Coincident attackers cluster into one pin that carries all of them and
  // wears a count.
  const markers = useMemo<Marker[]>(() => {
    const byPlace = new Map<string, Marker>();
    for (const attacker of located) {
      const point = projection([attacker.geo.lon as number, attacker.geo.lat as number]);
      if (!point) continue;
      const key = `${(attacker.geo.lat as number).toFixed(3)},${(attacker.geo.lon as number).toFixed(3)}`;
      const found = byPlace.get(key);
      if (found) found.attackers.push(attacker);
      else byPlace.set(key, { attackers: [attacker], xPct: (point[0] / VIEW_W) * 100, yPct: (point[1] / VIEW_H) * 100 });
    }
    return [...byPlace.values()];
  }, [located, projection]);

  const isBlocking = selected !== null && pending.has(selected.ip);
  const autoOff = attackerMethod === "off" || attackerMethod === "";

  return (
    <div className="g-sec-attackmap">
      {handledCount > 0 && (
        <div className="g-sec-attackmap-toggle">
          <button
            type="button"
            aria-pressed={showHandled}
            onClick={() => setShowHandled((v) => !v)}
          >
            {showHandled
              ? `Hide handled attackers (${handledCount})`
              : `Show handled attackers (${handledCount} hidden)`}
          </button>
          {!showHandled ? (
            <span className="g-sec-attackmap-toggle-note" data-testid="attackmap-toggle-note">
              {handledCount} attacker{handledCount === 1 ? "" : "s"} already blocked, hidden from
              this view.
            </span>
          ) : handledUnlocated > 0 ? (
            <span className="g-sec-attackmap-toggle-note" data-testid="attackmap-toggle-note">
              {handledUnlocated} of the {handledCount} {handledUnlocated === 1 ? "has" : "have"} no map
              position and {handledUnlocated === 1 ? "appears" : "appear"} only in the list below.
            </span>
          ) : null}
        </div>
      )}

      <div className="g-sec-attackmap-frame">
        <svg
          className="g-sec-attackmap-svg"
          viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
          role="img"
          aria-label="World map of attacker origins"
        >
          {WORLD.features.map((feature, i) => (
            <path
              key={i}
              d={path(feature as unknown as GeoPermissibleObjects) ?? undefined}
              className="g-sec-attackmap-country"
            />
          ))}
        </svg>
        <div className="g-sec-attackmap-markers">
          {markers.map(({ attackers: group, xPct, yPct }) => {
            const primary = group[0];
            // One still-live attacker keeps the whole pin red: it needs a look.
            const handled = group.every(isHandled);
            const size = radiusPx(Math.max(...group.map((a) => a.fails))) * 2;
            const many = group.length > 1;
            const ips = group.map((a) => a.ip).join(", ");
            return (
              <button
                key={primary.ip}
                type="button"
                data-testid="attack-marker"
                data-handled={handled ? "true" : "false"}
                data-count={group.length}
                className={`g-sec-attack-marker ${handled ? "g-sec-attack-marker--handled" : "g-sec-attack-marker--active"}`}
                style={{ left: `${xPct}%`, top: `${yPct}%`, width: size, height: size }}
                aria-label={many
                  ? `${group.length} attackers here: ${ips}. ${statusSentence(primary)}`
                  : `${primary.ip} — ${primary.fails} failed attempts. ${statusSentence(primary)}`}
                title={many ? `${group.length} attackers: ${ips}` : statusSentence(primary)}
                onClick={() => setSelected(primary)}
              >
                <span aria-hidden="true" className="g-sec-attack-marker-glyph">
                  {handled ? "✓" : "!"}
                </span>
                {many && (
                  <span className="g-sec-attack-marker-count" aria-hidden="true">
                    {group.length}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div className="g-sec-attackmap-legend">
        <span className="g-sec-attackmap-legend-item">
          <span aria-hidden="true" className="g-sec-attack-marker-swatch g-sec-attack-marker--active" />
          active — needs your attention
        </span>
        <span className="g-sec-attackmap-legend-item">
          <span aria-hidden="true" className="g-sec-attack-marker-swatch g-sec-attack-marker--handled" />
          handled — Vault already blocked it
        </span>
      </div>

      <p className="g-sec-attackmap-caption">
        A block is a permanent firewall rule on the host it protects — it stays in place unless
        someone removes it, and a returning address is blocked again automatically.{" "}
        {autoOff
          ? "Automatic handling of a new attacker is currently off, so a first-time address will not be blocked without a person acting."
          : `New attackers are currently handled automatically (policy: ${attackerMethod}).`}
      </p>

      {located.length > 0 && (
        <p className="g-sec-attackmap-caption">
          Positions are approximate — from IP geolocation, which places the address's hosting
          provider, not a device or a person.
        </p>
      )}

      {visible.length > 0 && (
        <div className="g-sec-attackmap-list" data-testid="attackers-list">
          <h3>Attackers ({visible.length})</h3>
          <ul>
            {[...located, ...unlocated].map((a) => (
              <li key={a.ip}>
                <button
                  type="button"
                  className="g-sec-attackmap-list-row"
                  onClick={() => setSelected(a)}
                >
                  <span className="mono">{a.ip}</span>
                  <span className="g-sec-attackmap-list-place">
                    {hasCoords(a)
                      ? [a.geo.city, a.geo.country].filter(Boolean).join(", ") || "—"
                      : "Location unknown"}
                  </span>
                  <NetworkBadge network={a.network} />
                  <span className="g-sec-attackmap-list-meta">{a.fails} fails</span>
                  <span
                    className={`g-sec-attackmap-list-status ${isHandled(a) ? "g-tone-plain" : "g-tone-alert"}`}
                  >
                    {statusSentence(a)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {unlocated.length > 0 && (
        <div className="g-sec-attackmap-unlocated" data-testid="attackers-unlocated">
          <h3>Location unknown ({unlocated.length})</h3>
          <p className="g-sec-attackmap-unlocated-reason">{UNLOCATED_REASON}</p>
        </div>
      )}

      <Drawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={selected?.ip ?? ""}
        subtitle={selected?.geo?.org || undefined}
      >
        {selected && (
          <>
            <dl className="g-facts">
              <div className="g-facts-row">
                <dt>IP</dt>
                <dd>{selected.ip}</dd>
              </div>
              <div className="g-facts-row">
                <dt>Organization</dt>
                <dd>{selected.geo?.org || "—"}</dd>
              </div>
              <div className="g-facts-row">
                <dt>Failed attempts</dt>
                <dd>{selected.fails}</dd>
              </div>
              <div className="g-facts-row">
                <dt>Days active</dt>
                <dd>{selected.days}</dd>
              </div>
              <div className="g-facts-row">
                <dt>Status</dt>
                <dd>{statusSentence(selected)}</dd>
              </div>
              {isHandled(selected) && (
                <div className="g-facts-row">
                  <dt>What that means</dt>
                  <dd>
                    Vault added a permanent firewall rule for this address on{" "}
                    {selected.hosts.length > 0 ? selected.hosts.join(", ") : "the affected host"}.
                    It stays blocked there unless someone removes the rule.
                  </dd>
                </div>
              )}
              <div className="g-facts-row">
                <dt>Last seen</dt>
                <dd>{ago(selected.last_seen)}</dd>
              </div>
              {selected.network !== null && (
                <div className="g-facts-row">
                  <dt>Network</dt>
                  <dd>
                    <NetworkBadge network={selected.network} />
                  </dd>
                </div>
              )}
            </dl>
            <button
              type="button"
              className="g-danger"
              disabled={!canWrite || isBlocking}
              title={canWrite ? undefined : writeTooltip}
              onClick={() => onBlock(selected)}
            >
              {isBlocking ? "Blocking…" : "Block this /24"}
            </button>
          </>
        )}
      </Drawer>
    </div>
  );
}

export default AttackMap;
