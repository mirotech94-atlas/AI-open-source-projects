// Task 69 (web): the camera census on the network strip and the per-network
// section head, and the drawer line that says WHY a device was filed as a
// camera. Components are rendered directly -- the counts come straight off
// `NetworkHealth`, so there is nothing here that needs a page's data flow.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { NetworkHealthStrip } from "../src/components/NetworkHealthStrip";
import { NetworkSection } from "../src/components/NetworkSection";
import type { DeviceCard, DevicesPayload, NetworkHealth, NetworkRef } from "../src/types";
import { NO_TILES } from "./tiles";

const ref: NetworkRef = { id: "net_home", slug: "home", name: "Home network", colour: "" };

const health: NetworkHealth = {
  ...ref,
  state: "good",
  status: "active",
  devices: 24,
  online: 19,
  unverified: 0,
  cameras: 11,
  cameras_online: 10,
  crit: 0,
  warn: 0,
  sensor_ok: true,
  is_primary: true,
};

function device(overrides: Partial<DeviceCard>): DeviceCard {
  return {
    mac: "aa:bb:cc:dd:ee:01",
    ip: "192.168.1.182",
    name: "weme",
    label: "Reolink camera",
    vendor: "Reolink Innovation Limited",
    kind: "camera",
    group: "Cameras",
    trusted: false,
    always_on: false,
    quarantined: false,
    parent: null,
    risk: 10,
    presence: "ONLINE",
    online: true,
    first_seen: "2026-09-01T10:00:00-04:00",
    last_seen: "2026-09-10T10:00:00-04:00",
    ports: [],
    unverified: false,
    randomized: false,
    network: ref,
    online_7d: null,
    camera_reasons: ["camera vendor (Reolink Innovation Limited)", "answers on 554 (rtsp)"],
    ...overrides,
  };
}

const payload: DevicesPayload = {
  groups: [{ group: "Cameras", devices: [device({})], network: ref }],
  chips: [{ group: "Cameras", count: 11, online: 10, network_id: ref.id }],
  total: 24,
  online: 19,
  unverified: 0,
  cameras: 11,
  cameras_online: 10,
  tiles: NO_TILES,
  networks: [health],
  charts: [],
};

/** The strip reads `useMe()` for the tenant it stores the selected network
 *  under, so it needs a query client even though nothing here signs in. */
function strip(networks: NetworkHealth[]) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <NetworkHealthStrip networks={networks} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("camera counts", () => {
  it("puts the camera census on the network pill, in the text and the label", () => {
    strip([health]);
    const pill = screen.getByTestId("network-pill");
    expect(pill).toHaveTextContent("10/11 cameras");
    expect(pill).toHaveAccessibleName(/10 of 11 cameras answering/);
  });

  it("leaves the pill alone for a network with no cameras", () => {
    strip([{ ...health, cameras: 0, cameras_online: 0 }]);
    expect(screen.getByTestId("network-pill")).not.toHaveTextContent("cameras");
  });

  it("shows the camera count in the per-network section head", () => {
    render(
      <NetworkSection
        network={health}
        payload={payload}
        selectedKind={null}
        onSelectKind={() => {}}
        onOpen={() => {}}
      />,
    );
    const section = screen.getByTestId("network-section");
    expect(within(section).getByRole("heading", { name: "Home network" })).toBeInTheDocument();
    expect(section).toHaveTextContent("10/11 cameras answering");
  });
});
