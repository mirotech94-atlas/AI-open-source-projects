import { stateTone } from "../lib/format";
import type { SecurityCheck, WifiGuard } from "../types";

export type WifiGuardPanelProps = {
  wifi: WifiGuard;
  /** Opens the resolve-or-leave-it drawer for one failing Wi-Fi guard row
   *  (owner request: clicking a failing check like "evil twin" should open
   *  detail). Omitted, the panel renders exactly as before. */
  onOpenCheck?: (check: SecurityCheck) => void;
};

type Fact = {
  key: string;
  label: string;
  detail: string;
  passed: boolean;
  /** The real `wifi.rows` entry this fact is drawn from, when there is one
   *  — only a failing fact with a row behind it is clickable. */
  row?: SecurityCheck;
};

/** The five facts a homeowner actually cares about, derived straight from
 *  `WifiGuard`'s own fields — never re-fetched or re-derived server-side
 *  data client-side. Four of them (everything but encryption) are also one
 *  of `wifi.rows`, carried along here so a failing one can open that row's
 *  own detail rather than a second, disconnected view of the same fact. */
function factsFrom(wifi: WifiGuard): Fact[] {
  const rowByKey = new Map(wifi.rows.map((r) => [r.key, r]));
  return [
    {
      key: "encryption",
      label: "SSID + encryption",
      detail: `${wifi.ssid || "—"} · ${wifi.security || "unknown"}`,
      passed: wifi.security !== "" && wifi.security.toUpperCase() !== "OPEN" && wifi.security.toUpperCase() !== "WEP",
    },
    {
      key: "evil-twin",
      label: "Evil twin",
      detail: wifi.evil_twin
        ? `Rogue AP seen on channel${wifi.twin_channels.length > 1 ? "s" : ""} ${wifi.twin_channels.join(", ")}`
        : "No lookalike access point seen",
      passed: !wifi.evil_twin,
      row: rowByKey.get("wifi:evil_twin"),
    },
    {
      key: "rogue-dhcp",
      label: "Rogue DHCP",
      detail: wifi.rogue_dhcp
        ? `Unexpected DHCP server ${wifi.dhcp_server} (expected ${wifi.expected_gateway})`
        : `DHCP server matches the expected gateway (${wifi.expected_gateway || "—"})`,
      passed: !wifi.rogue_dhcp,
      row: rowByKey.get("wifi:rogue_dhcp"),
    },
    {
      key: "gateway-drift",
      label: "Gateway integrity",
      detail:
        wifi.default_gateway !== "" && wifi.expected_gateway !== "" && wifi.default_gateway !== wifi.expected_gateway
          ? `Traffic leaves through ${wifi.default_gateway} (expected ${wifi.expected_gateway})`
          : wifi.default_gateway
            ? `Matches the expected gateway (${wifi.expected_gateway || wifi.default_gateway})`
            : "Not reported on this network",
      passed:
        wifi.default_gateway === "" ||
        wifi.expected_gateway === "" ||
        wifi.default_gateway === wifi.expected_gateway,
      row: rowByKey.get("wifi:gateway_drift"),
    },
    {
      key: "unknown-devices",
      label: "Unknown devices",
      detail:
        wifi.unknown_devices > 0
          ? `${wifi.unknown_devices} unverified device${wifi.unknown_devices === 1 ? "" : "s"} on the network`
          : "Every device on the network is recognized",
      passed: wifi.unknown_devices === 0,
      row: rowByKey.get("wifi:unverified"),
    },
  ];
}

/** The Wi-Fi guard panel: one verdict line with tone, then the five facts a
 *  homeowner actually cares about as pass/fail rows. */
export function WifiGuardPanel({ wifi, onOpenCheck }: WifiGuardPanelProps) {
  const tone = stateTone(wifi.state);
  const facts = factsFrom(wifi);

  return (
    <div className="g-sec-wifi" data-testid="wifi-guard" data-network={wifi.network?.id ?? ""}>
      {wifi.network !== null && <h3 className="g-sec-wifi-network">{wifi.network.name}</h3>}
      <p className={`g-sec-wifi-verdict g-tone-${tone}`} data-testid="wifi-verdict" aria-live="polite">
        {wifi.verdict}
      </p>
      <ul className="g-sec-wifi-facts">
        {facts.map((fact) => {
          // Only a failing fact whose row Vault actually verified
          // (`passed === false`, never `null`) opens a drawer -- a network
          // with no sensor of its own reports `passed: null` on every row,
          // and offering detail on a fact nobody has checked would be the
          // same false confidence `_secondary_guard` is written to avoid.
          const openHandler =
            !fact.passed && fact.row !== undefined && fact.row.passed === false ? onOpenCheck : undefined;
          const body = (
            <span className="g-sec-wifi-fact-body">
              <span className="g-sec-wifi-fact-label">
                {fact.label}
                {fact.row?.decision?.status === "left" && (
                  <span className="g-sec-check-left-badge">Left as-is</span>
                )}
              </span>
              <span className="g-sec-wifi-fact-detail">{fact.detail}</span>
            </span>
          );
          // Evil twin is the one fact Vault itself hedges on — it cannot
          // tell a malicious access point from a mesh node or repeater by
          // radio alone (the drawer says so in `why_it_matters`). Styling a
          // failing reading here the same confirmed red as rogue DHCP or a
          // gateway swap would tell the eye something the copy does not
          // stand behind, so it stays amber — worth checking, not a
          // confirmed attack — until a person confirms it.
          const failTone = fact.key === "evil-twin" ? "warn" : "alert";
          return (
            <li key={fact.key} className="g-sec-wifi-fact" data-testid="wifi-fact">
              <span className={`g-sec-passfail g-tone-${fact.passed ? "good" : failTone}`} aria-hidden="true">
                {fact.passed ? "✓" : "✗"}
              </span>
              {openHandler ? (
                <button
                  type="button"
                  className="g-sec-check-row-btn"
                  onClick={() => openHandler(fact.row as SecurityCheck)}
                >
                  {body}
                </button>
              ) : (
                body
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default WifiGuardPanel;
