/** The attack map's handled-attackers toggle promised N and the map showed
 *  fewer once revealed (seen in production: "5 hidden", then 3 dots). Two
 *  causes, both fixed here:
 *   - the hub keys attackers by (host, ip), so one address that hit two
 *     hosts arrived as two rows -- counted twice, drawn twice on the same
 *     spot (one visible dot), with a duplicate React key;
 *   - an attacker with no geolocation is counted but never gets a marker,
 *     and nothing beside the toggle said so. */
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AttackMap } from "../src/components/AttackMap";
import type { Attacker } from "../src/types";

const geoNL = { country: "Netherlands", country_code: "NL", city: "Amsterdam", org: "Some Hosting BV",
                isp: "Some Hosting", lat: 52.37, lon: 4.9, hosting: true, reverse: "" };
const geoBR = { country: "Brazil", country_code: "BR", city: "Sao Paulo", org: "Another ISP",
                isp: "Another ISP", lat: -23.55, lon: -46.63, hosting: false, reverse: "" };

function attacker(over: Partial<Attacker>): Attacker {
  return {
    ip: "1.2.3.4", host: "vps1", fails: 10, days: 1,
    first_seen: "2026-09-08T00:00:00-04:00", last_seen: "2026-09-09T10:00:00-04:00",
    geo: geoNL, action: "block_ip", action_ts: "2026-09-09T12:05:00-04:00",
    alert_key: "sec:attacker:vps1:1.2.3.4", network: null,
    ...over,
  };
}

describe("AttackMap handled-attackers toggle", () => {
  it("counts one attacker per address even when the hub reports it once per host, and draws it once", async () => {
    const rows = [
      attacker({ ip: "1.2.3.4", host: "vps1", fails: 40 }),
      attacker({ ip: "1.2.3.4", host: "vps2", fails: 30, alert_key: "sec:attacker:vps2:1.2.3.4" }),
      attacker({ ip: "5.6.7.8", host: "vps1", geo: geoBR, alert_key: "sec:attacker:vps1:5.6.7.8" }),
    ];
    render(<AttackMap attackers={rows} onBlock={() => {}} />);

    const toggle = screen.getByRole("button", { name: /show handled attackers/i });
    expect(toggle).toHaveAccessibleName(/2 hidden/);
    await userEvent.click(toggle);

    const markers = screen.getAllByTestId("attack-marker");
    expect(markers).toHaveLength(2);
    const list = screen.getByTestId("attackers-list");
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText("1.2.3.4").closest("li")).toHaveTextContent("70 fails");

    // The drawer names every host the address hit, not just the first row's.
    await userEvent.click(within(list).getByText("1.2.3.4"));
    expect(screen.getByRole("dialog")).toHaveTextContent(/vps1, vps2/);
  });

  it("says how many revealed attackers have no map position, so fewer dots than the count is explained", async () => {
    const rows = [
      attacker({ ip: "1.2.3.4" }),
      attacker({ ip: "5.6.7.8", geo: geoBR }),
      attacker({ ip: "9.9.9.9", host: "vps2", geo: null }),
      attacker({ ip: "9.9.9.10", host: "vps2", geo: null }),
      attacker({ ip: "9.9.9.11", host: "vps2", geo: null }),
    ];
    render(<AttackMap attackers={rows} onBlock={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /5 hidden/ }));

    expect(screen.getAllByTestId("attack-marker")).toHaveLength(2);
    expect(screen.getByTestId("attackmap-toggle-note")).toHaveTextContent(
      /3 of the 5 have no map position and appear only in the list below/i,
    );
  });

  it("clusters attackers that share a location into one pin that wears a count", () => {
    // The live case: five handled attackers -- two Istanbul /24 neighbours,
    // two Cornelius (US) /24 neighbours, one Santiago. Five attackers, three
    // places; the map used to draw all five on three pixels and show three.
    const geoTR = { ...geoNL, lat: 41.008, lon: 28.978, city: "Istanbul" };
    const geoUS = { ...geoNL, lat: 35.459, lon: -80.854, city: "Cornelius" };
    const geoCL = { ...geoNL, lat: -33.452, lon: -70.654, city: "Santiago" };
    render(<AttackMap attackers={[
      attacker({ ip: "192.0.2.198", geo: geoTR }), attacker({ ip: "192.0.2.197", geo: geoTR }),
      attacker({ ip: "203.0.113.116", geo: geoUS }), attacker({ ip: "203.0.113.118", geo: geoUS }),
      attacker({ ip: "198.51.100.104", geo: geoCL }),
    ]} onBlock={() => {}} />);
    const toggle = screen.queryByRole("button", { name: /show handled/i });
    if (toggle) fireEvent.click(toggle);          // reveal handled pins whatever the fixture default
    const markers = screen.getAllByTestId("attack-marker");
    expect(markers).toHaveLength(3);
    expect(markers.map((m) => m.getAttribute("data-count")).sort()).toEqual(["1", "2", "2"]);
    const tr = markers.find((m) => m.getAttribute("aria-label")?.includes("192.0.2.198"))!;
    expect(tr.getAttribute("aria-label")).toContain("192.0.2.197");   // both IPs named on the one pin
    expect(tr.getAttribute("data-count")).toBe("2");
  });
});
