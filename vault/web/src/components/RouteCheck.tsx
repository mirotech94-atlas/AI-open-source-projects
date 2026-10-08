import { Badge } from "../design";
import type { WifiGuard } from "../types";
import "../styles/routecheck.css";

/**
 * Vault Portal v2's "Route check" beside the network map: the four ways
 * out of a network that a man-in-the-middle would have to bend, re-checked
 * on every scan. Each row is a fact the sensor actually observed on the
 * selected network — the same facts the Security tab's Wi-Fi guard grades —
 * so this card can never disagree with that one.
 *
 * A guard with no reading yet says "not checked yet", never "as expected":
 * absence of evidence is not evidence of a healthy route.
 */
type Row = { key: string; label: string; state: "good" | "alert" | "plain"; word: string; detail: string };

export function routeRows(g: WifiGuard | null): Row[] {
  if (!g || !g.expected_gateway) {
    return [
      { key: "none", label: "Route check", state: "plain", word: "not checked yet",
        detail: "No sensor on this network has reported its way out to the internet." },
    ];
  }
  const gwOk = !g.default_gateway || g.default_gateway === g.expected_gateway;
  const dhcpOk = !g.rogue_dhcp;
  const twinOk = !g.evil_twin;
  // Item 19: two more ways out that a man-in-the-middle has to bend, both
  // read from facts the sensor already reports. Neither is invented: an
  // unreported value says "not checked yet", never "as expected".
  const sec = (g.security || "").trim();
  const secLower = sec.toLowerCase();
  const openWifi = sec !== "" && (secLower === "none" || secLower === "open" || secLower.includes("wep"));
  const encKnown = sec !== "";
  return [
    { key: "router", label: "One router, as expected", state: gwOk ? "good" : "alert",
      word: gwOk ? "as expected" : "changed",
      detail: gwOk ? `Default route is the router at ${g.expected_gateway}.`
                   : `Default route is ${g.default_gateway}, not the router at ${g.expected_gateway}.` },
    { key: "gwmac", label: "Gateway MAC unchanged", state: g.gateway_mac ? "good" : "plain",
      word: g.gateway_mac ? "unchanged" : "not recorded",
      // A changed MAC raises its own CRIT alert (home:gw-mac); this row shows
      // the value being watched so a person can compare it with the router.
      detail: g.gateway_mac ? `Router answers as ${g.gateway_mac}.` : "The sensor has not recorded the router's hardware address yet." },
    { key: "dhcp", label: "No second DHCP server", state: dhcpOk ? "good" : "alert",
      word: dhcpOk ? "as expected" : "rogue DHCP",
      detail: dhcpOk ? "Every lease came from the router." : `A lease came from ${g.dhcp_server}, not the router.` },
    { key: "twin", label: "No one else broadcasting your Wi-Fi name", state: twinOk ? "good" : "alert",
      word: twinOk ? "as expected" : "evil twin",
      detail: twinOk ? (g.ssid ? `Only your access point is announcing ${g.ssid}.` : "No second access point is using this name.")
                     : `Another access point is broadcasting ${g.ssid || "your network name"} on channel(s) ${g.twin_channels.join(", ") || "?"}.` },
    // The link itself. An open or WEP network means anyone within range
    // reads every unencrypted thing every device sends -- no gateway needs
    // bending at all, which is why it belongs beside the other three.
    { key: "encryption", label: "Traffic over the air is encrypted",
      state: !encKnown ? "plain" : openWifi ? "alert" : "good",
      word: !encKnown ? "not checked yet" : openWifi ? "readable by anyone" : sec,
      detail: !encKnown
        ? "The sensor has not reported this network's encryption yet."
        : openWifi
          ? `This network uses ${sec}. Anyone within range can read what devices send over it.`
          : `Devices join over ${sec}, so traffic over the air is encrypted.` },
    // Devices Vault cannot account for. A machine on the network it has
    // never identified is the one that could be doing the bending.
    { key: "unknown", label: "Every device on the network is accounted for",
      state: g.unknown_devices > 0 ? "alert" : "good",
      word: g.unknown_devices > 0 ? `${g.unknown_devices} unidentified` : "as expected",
      detail: g.unknown_devices > 0
        ? `${g.unknown_devices} device${g.unknown_devices === 1 ? " is" : "s are"} on this network that Vault has not identified. Confirm each one is yours.`
        : "Vault recognises every device currently on this network." },
  ];
}

export function RouteCheck({ guard }: { guard: WifiGuard | null }) {
  const rows = routeRows(guard);
  return (
    <section className="g-route" data-testid="route-check">
      <header className="g-route-head">
        <h3>Route check</h3>
        <span>Vault re-checks the way out on every scan.</span>
      </header>
      <ul className="g-route-rows">
        {rows.map((r) => (
          <li key={r.key} data-state={r.state}>
            <span className="g-route-label">{r.label}</span>
            <Badge tone={r.state}>{r.word}</Badge>
            <span className="g-route-detail">{r.detail}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default RouteCheck;
