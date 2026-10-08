import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AssetInventoryTable, AssetTiles, inventoryRows } from "../src/components/AssetInventory";
import { routeRows } from "../src/components/RouteCheck";
import type { DeviceCard, HostCard, SiteCard, WifiGuard } from "../src/types";

// The v2 Assets page puts one word next to every row. These pin the word to
// the field that earns it, and pin the words that must NOT be handed out.

function host(over: Partial<HostCard> = {}): HostCard {
  return {
    name: "vps1", address: "10.0.0.5", reachable: true, hostname: "vps1.example", uptime: "3d",
    load1: 0.1, load_per_core: 0.1, disk_pct: 20, mem_pct: 30, failed_ssh_24h: 0, banned: 0,
    fail2ban: true, reboot_required: false, pending_updates: 0, security_updates: 0, cve_fixable: 0,
    hardening: { score: 100, grade: "A", checks: [] }, ports: [], docker: [], services: [], gauges: [],
    stale: false, stale_note: "", last_checked: new Date().toISOString(), ...over,
  };
}
function dev(over: Partial<DeviceCard> = {}): DeviceCard {
  return {
    mac: "aa:bb", ip: "10.0.0.9", name: "printer", label: "", vendor: "HP", kind: "printer", group: "printers",
    trusted: true, always_on: false, quarantined: false, parent: null, risk: 0, presence: "ONLINE", online: true,
    first_seen: "", last_seen: new Date().toISOString(), ports: [], unverified: false, randomized: false,
    network: null, online_7d: null, camera_reasons: [], ...over,
  };
}
function site(over: Partial<SiteCard> = {}): SiteCard {
  return {
    name: "shop", url: "https://shop.example", ok: true, code: 200, ms: 200, uptime_24h: 100, uptime_7d: 100,
    uptime_24h_checks: 0, uptime_7d_checks: 0, uptime_24h_planned: 0, uptime_7d_planned: 0, latency_spark: [],
    cert_days: 60, cert_expires: "", cert_issuer: "", dns_a: [], headers: {} as SiteCard["headers"],
    incidents_7d: 0, public: false, stale: false, stale_note: "", last_checked: "", ...over,
  };
}

describe("inventory posture", () => {
  it("a server is clean only with nothing outstanding", () => {
    expect(inventoryRows([host()], [])[0].posture.label).toBe("clean");
    expect(inventoryRows([host({ security_updates: 2, cve_fixable: 1 })], [])[0].posture.label).toBe("3 findings");
    expect(inventoryRows([host({ reboot_required: true })], [])[0].posture.label).toBe("1 finding");
  });
  it("an unreachable server is unreachable, not clean", () => {
    expect(inventoryRows([host({ reachable: false })], [])[0].posture).toEqual({ label: "unreachable", tone: "alert" });
  });
  it("a server the sensor has not read is unknown", () => {
    expect(inventoryRows([host({ reachable: null })], [])[0].posture.label).toBe("unknown");
  });
  it("a device that is asleep is unknown, never clean", () => {
    // Absence of evidence is not a clean bill.
    expect(inventoryRows([], [dev({ online: false })])[0].posture.label).toBe("unknown");
  });
  it("quarantine outranks everything, then unverified, then a risky port", () => {
    const risky = { port: 23, since: "", risky: true, service: "telnet" };
    expect(inventoryRows([], [dev({ quarantined: true, unverified: true, ports: [risky] })])[0].posture.label).toBe("quarantined");
    expect(inventoryRows([], [dev({ unverified: true, ports: [risky] })])[0].posture.label).toBe("unverified");
    expect(inventoryRows([], [dev({ ports: [risky] })])[0].posture.label).toBe("open port");
  });
  it("platform is what Vault actually reads, never an invented OS", () => {
    const rows = inventoryRows([host({ hostname: "vps1.example" })], [dev({ vendor: "Reolink" })]);
    expect(rows.map((r) => r.platform).sort()).toEqual(["Reolink", "vps1.example"]);
    expect(JSON.stringify(rows)).not.toMatch(/Ubuntu|macOS|Windows/);
  });
  it("sorts the row that needs a look to the top", () => {
    const rows = inventoryRows([host()], [dev({ quarantined: true, name: "bad" }), dev({ name: "ok", mac: "cc:dd" })]);
    expect(rows[0].name).toBe("bad");
  });
  it("an unverified device answers to the Unknown chip", () => {
    expect(inventoryRows([], [dev({ unverified: true })])[0].bucket).toBe("unknown");
  });
});

describe("asset tiles", () => {
  it("counts only certificates Vault has read", () => {
    render(<AssetTiles devices={{ total: 5, online: 4 }} hosts={[host()]}
                       sites={[site({ cert_days: 10 }), site({ name: "b", cert_days: null })]}
                       unverified={0} certWarnDays={30} />);
    // one read, one unread: the tile says so in Coverage's own framing, and
    // takes the warning tone for the unread one -- it must not read as fine.
    const tile = screen.getByText("Certificates").closest("li")!;
    expect(tile.textContent).toMatch(/1 of 2 read/);
    expect(tile.textContent).toMatch(/1 inside 30 d/);
    expect(tile.dataset.warn).toBe("1");
  });
  it("says all read, and stays quiet, when every site's certificate is read", () => {
    render(<AssetTiles devices={{ total: 0, online: 0 }} hosts={[]}
                       sites={[site({ cert_days: 60 }), site({ name: "b", cert_days: 90 })]}
                       unverified={0} certWarnDays={30} />);
    const tile = screen.getByText("Certificates").closest("li")!;
    expect(tile.textContent).toMatch(/all 2 read · 0 inside 30 d/);
    expect(tile.dataset.warn).toBeUndefined();
  });
  it("never says all reporting when one server is down", () => {
    render(<AssetTiles devices={{ total: 0, online: 0 }} hosts={[host(), host({ name: "b", reachable: false })]}
                       sites={[]} unverified={0} certWarnDays={30} />);
    expect(screen.getByText("Servers").closest("li")!.textContent).toMatch(/1 reporting/);
  });
});

describe("inventory table", () => {
  it("chips carry the counts of the rows they filter", () => {
    const rows = inventoryRows([host()], [dev(), dev({ unverified: true, mac: "cc:dd" })]);
    render(<AssetInventoryTable rows={rows} filter="all" onFilter={() => {}} />);
    expect(screen.getByRole("button", { name: /All · 3/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Servers · 1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Unknown · 1/ })).toBeInTheDocument();
  });
});

describe("route check", () => {
  const guard = (over: Partial<WifiGuard> = {}): WifiGuard => ({
    verdict: "", state: "good", ssid: "Home", security: "WPA2", channel: 6, phy: "", signal: null,
    evil_twin: false, twin_channels: [], rogue_dhcp: false, dhcp_server: "", expected_gateway: "192.168.1.1",
    default_gateway: "192.168.1.1", gateway_mac: "aa:bb:cc:dd:ee:ff", unknown_devices: 0, rows: [], network: null, ...over,
  });
  it("with no reading it says not checked, never as expected", () => {
    const rows = routeRows(null);
    expect(rows).toHaveLength(1);
    expect(rows[0].word).toBe("not checked yet");
    expect(JSON.stringify(rows)).not.toMatch(/as expected/);
  });
  it("a healthy route reads as expected on every row", () => {
    expect(routeRows(guard()).every((r) => r.state === "good")).toBe(true);
  });
  it("a rogue DHCP server and a drifted gateway each turn their row red", () => {
    const r = routeRows(guard({ rogue_dhcp: true, dhcp_server: "192.168.1.50", default_gateway: "192.168.1.50" }));
    expect(r.find((x) => x.key === "dhcp")!.state).toBe("alert");
    expect(r.find((x) => x.key === "router")!.state).toBe("alert");
  });
  it("an unrecorded gateway MAC is not reported as unchanged", () => {
    expect(routeRows(guard({ gateway_mac: "" })).find((x) => x.key === "gwmac")!.word).toBe("not recorded");
  });
});
