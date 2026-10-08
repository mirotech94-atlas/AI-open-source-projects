// "Is it mine?" on the device drawer: the verdict leads, the reasons are the
// evidence a person can push back on. The drawer is the surface where an
// unnamed randomized-MAC device used to mean a walk round the house.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { DeviceDetail, DeviceTraffic } from "../src/types";

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, deviceDetail: vi.fn(), deviceTraffic: vi.fn() };
});
vi.mock("../src/lib/auth", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/auth")>("../src/lib/auth");
  return { ...actual, useMe: () => ({ data: { user: { username: "u" }, tenants: [], active_tenant: { role: "admin" } } }) };
});

import { ToastProvider } from "../src/design";
import { deviceDetail, deviceTraffic } from "../src/lib/api";
import { DeviceDrawer } from "../src/components/DeviceDrawer";

const EMPTY_TRAFFIC: DeviceTraffic = {
  mode: "none", mac: "", window_hours: 24, bytes_in: 0, bytes_out: 0, peers: [], hourly: [],
};

const DETAIL = {
  device: { mac: "ce:0e:c4:3a:35:cb", ip: "192.168.1.198", name: "", label: "", vendor: "private/randomized MAC", kind: "unknown", trusted: false, quarantined: false, always_on: false, presence: "SLEEPING", risk: 0, first_seen: "", last_seen: "", ports: [], parent: null, network: null, camera: null, children: 0 },
  timeline: [], presence: { online_pct_24h: 0, online_pct_7d: 0, hours: [], typical_hours: [], days: [], strip: [] },
  ports: [], dns: { queries_24h: 0, blocked_24h: 0, top: [], blocked_top: [] }, alerts: [], children: [], events: [],
  familiarity: {
    mac: "ce:0e:c4:3a:35:cb", name: "", label: "", ip: "192.168.1.198", vendor: "private/randomized MAC",
    randomized_mac: true, trusted: false, verdict: "resident",
    summary: "192.168.1.198 behaves like a device that lives here: seen on 5 of the last 5 days; 52% of sightings are overnight (midnight to 6am) -- it sleeps here.",
    reasons: ["seen on 5 of the last 5 days", "52% of sightings are overnight (midnight to 6am) -- it sleeps here", "not seen for 2 days"],
    days_seen: 5, lookback_days: 30, span_days: 5, first_seen: "2026-09-10T13:21", last_seen: "2026-09-13T20:54", silent_days: 2,
    overnight_share: 0.52, typical_hours: [0, 1, 2, 23], joins: 6, leaves: 5, dns_queries: 0, top_dns: [], platform_hint: "",
    name_siblings: [{ mac: "aa:bb:cc:dd:ee:ff", ip: "192.168.1.170", trusted: true, first_seen: "", last_seen: "" }], other_networks: [],
  },
} as unknown as DeviceDetail;

describe("the device drawer's 'Is it mine?' tab", () => {
  it("leads with the verdict and lists the evidence", async () => {
    (deviceDetail as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(DETAIL);
    (deviceTraffic as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(EMPTY_TRAFFIC);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ToastProvider>
          <MemoryRouter>
            <DeviceDrawer mac="ce:0e:c4:3a:35:cb" devices={[]} onClose={() => {}} />
          </MemoryRouter>
        </ToastProvider>
      </QueryClientProvider>,
    );
    await userEvent.click(await screen.findByRole("tab", { name: "Is it mine?" }));
    const tab = screen.getByTestId("device-familiar");
    expect(tab).toHaveTextContent("Lives here");
    expect(tab).toHaveTextContent("it sleeps here");
    expect(tab).toHaveTextContent("not seen for 2 days");
    expect(tab).toHaveTextContent("aa:bb:cc:dd:ee:ff");
    expect(tab).toHaveTextContent("trusted");
  });
});
