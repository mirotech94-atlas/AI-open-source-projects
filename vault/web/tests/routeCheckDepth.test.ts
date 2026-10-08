import { describe, expect, it } from "vitest";
import { routeRows } from "../src/components/RouteCheck";
import type { WifiGuard } from "../src/types";

const guard = (over: Partial<WifiGuard> = {}): WifiGuard => ({
  verdict: "ok", state: "good", ssid: "Home", security: "WPA2", channel: 6, phy: "ax",
  signal: -50, evil_twin: false, twin_channels: [], rogue_dhcp: false, dhcp_server: "",
  expected_gateway: "192.168.1.1", default_gateway: "192.168.1.1", gateway_mac: "aa:bb:cc:dd:ee:ff",
  unknown_devices: 0, rows: [], network: null, ...over,
});

describe("route check depth (item 19)", () => {
  it("covers more than the gateway, DHCP and evil twin", () => {
    const keys = routeRows(guard()).map((r) => r.key);
    expect(keys).toEqual(["router", "gwmac", "dhcp", "twin", "encryption", "unknown"]);
  });

  it("calls an open network readable by anyone", () => {
    // No gateway needs bending at all: anyone in range reads what devices
    // send. That is why it sits beside the other three.
    const row = routeRows(guard({ security: "none" })).find((r) => r.key === "encryption")!;
    expect(row.state).toBe("alert");
    expect(row.detail).toMatch(/anyone within range/i);
  });

  it("treats WEP as broken, not as encryption", () => {
    expect(routeRows(guard({ security: "WEP" })).find((r) => r.key === "encryption")!.state).toBe("alert");
  });

  it("says 'not checked yet' rather than 'as expected' when encryption is unreported", () => {
    // Absence of evidence is not evidence of a healthy route.
    const row = routeRows(guard({ security: "" })).find((r) => r.key === "encryption")!;
    expect(row.state).toBe("plain");
    expect(row.word).toBe("not checked yet");
  });

  it("raises unidentified devices, because that is who would do the bending", () => {
    const row = routeRows(guard({ unknown_devices: 3 })).find((r) => r.key === "unknown")!;
    expect(row.state).toBe("alert");
    expect(row.word).toContain("3");
  });

  it("still says nothing at all when no sensor has reported the network", () => {
    expect(routeRows(guard({ expected_gateway: "" })).map((r) => r.key)).toEqual(["none"]);
  });
});
