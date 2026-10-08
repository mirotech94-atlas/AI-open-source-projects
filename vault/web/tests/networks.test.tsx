// Task 53: several networks per tenant, surfaced across the app.
//
// One file for the cross-page behaviour, because the interesting cases are all
// the same question asked of a different tab: does this surface tell the two
// networks apart, and does its query carry `?network=`. Each page's own suite
// still covers its single-network behaviour — which must be unchanged, and is
// what `networks: []` in those fixtures asserts.
//
// The read mocks record the path they *would* have fetched, the same way
// `assets.test.tsx` records writes: nothing in this suite stubs `fetch`, so the
// transport story stays the one every other file tells.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { axe, toHaveNoViolations } from "jest-axe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import Assets from "../src/pages/Assets";
import Onboarding from "../src/pages/Onboarding";
import Security from "../src/pages/Security";
import Topology from "../src/pages/Topology";
import { Networks } from "../src/pages/Settings/Networks";
import type {
  DeviceCard,
  DevicesPayload,
  Network,
  NetworkHealth,
  NetworkRef,
  SecurityPayload,
  SettingsPayload,
  TopologyPayload,
  WifiGuard,
} from "../src/types";

// `tests/a11y.test.tsx` sweeps every tab in its single-network state; the
// two-network Assets view adds a chooser, section headings and status pills
// that only exist here, so it gets its own axe pass at the bottom of this file.
expect.extend(toHaveNoViolations);
const axeOptions = { rules: { "color-contrast": { enabled: false } }, iframes: false };

// --- live feed: every page here subscribes, none of these cases fire a frame

vi.mock("../src/lib/live", () => ({
  useLiveFeed: () => ({ status: "open" as const, last: null, on: () => () => {}, dropped: 0 }),
}));

// --- recorded reads and writes ----------------------------------------------

const { queriedPaths, postedCalls } = vi.hoisted(() => ({
  queriedPaths: [] as string[],
  postedCalls: [] as { path: string; body: unknown }[],
}));

function queried(): string[] {
  return queriedPaths;
}

function posted(path: string): unknown {
  return [...postedCalls].reverse().find((c) => c.path === path)?.body;
}

function q(network?: string | null): string {
  return network === null || network === undefined ? "" : `?network=${network}`;
}

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    devicesPayload: vi.fn(),
    deviceDetail: vi.fn(),
    screenTimePayload: vi.fn(),
    sitesPayload: vi.fn().mockResolvedValue({ sites: [] }),
    hostsPayload: vi.fn().mockResolvedValue({ hosts: [] }),
    settingsPayload: vi.fn(),
    securityPayload: vi.fn(),
    topologyPayload: vi.fn(),
    trafficOverview: vi.fn(),
    deviceTraffic: vi.fn(),
    // `Topology`/`Assets` both read the camera-census gaps off this — a
    // network-less, gap-less default keeps every case here about networks,
    // not cameras, unless a case opts in with its own override.
    overviewPayload: vi.fn().mockResolvedValue({
      ts: null, age_s: null, score: null, grade: "", potential: null, verdict: "",
      next_action: null, deductions: [], strip: [], timeline: [],
      score_trend: { name: "score", points: [], color: null }, questions: [],
      alerts: { open: 0, crit: 0, warn: 0, info: 0, acked: 0, snoozed: 0 },
      devices: { total: 0, online: 0, sleeping: 0, unverified: 0, quarantined: 0 },
      sensor: null, networks: [], chat_example_ip: "",
      score_trend_7d: { name: "score", points: [], color: null },
      assets: { total: 0, servers: 0, sites: 0 }, changed_24h: 0,
      incidents: { open: 0, crit: 0, warn: 0 }, incident_briefs: [],
      recs: { open: 0, critical: 0, high: 0, top: "" },
      health: {
        sensor_state: "online", run_seconds: null, scans_last_hour: null,
        sensors_active: 1, sensors_total: 1, feeds: [], last_event_ts: "",
        last_event_age_s: null, autofix_enabled: false, autofix_24h: 0,
        notify_channel: "", dns_filter_state: "on", dns_blocked_24h: 0,
      },
      paths: [],
      cameras: { known: 0, online: 0, expected: 0, exposed: false, exposed_ports: [], gaps: [] },
    }),
    networksPayload: vi.fn(),
    saveSettingsSection: vi.fn().mockResolvedValue({ ok: true }),
    updateDevice: vi.fn().mockResolvedValue({ ok: true }),
    createNetwork: vi.fn((body: Record<string, unknown>) => {
      postedCalls.push({ path: "/api/v1/networks", body });
      return Promise.resolve({ ok: true });
    }),
    updateNetwork: vi.fn((id: string, body: Record<string, unknown>) => {
      postedCalls.push({ path: `/api/v1/networks/${id}`, body });
      return Promise.resolve({ ok: true });
    }),
    deleteNetwork: vi.fn((id: string, reassign?: string | null) => {
      postedCalls.push({ path: `/api/v1/networks/${id}${reassign ? `?reassign=${reassign}` : ""}`, body: {} });
      return Promise.resolve({ ok: true });
    }),
  };
});

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    me: vi.fn(),
    generateEnrolCode: vi.fn(),
  };
});

import {
  deviceTraffic,
  devicesPayload,
  networksPayload,
  screenTimePayload,
  securityPayload,
  settingsPayload,
  topologyPayload,
  trafficOverview,
} from "../src/lib/api";
import { generateEnrolCode, me } from "../src/api";
import { NO_TILES } from "./tiles";

// --- fixtures ---------------------------------------------------------------

const officeRef: NetworkRef = { id: "net_office", slug: "office-wi-fi", name: "Office Wi-Fi", colour: "" };
const labRef: NetworkRef = { id: "net_lab", slug: "lab", name: "Lab", colour: "" };

const officeHealth: NetworkHealth = {
  ...officeRef,
  state: "good",
  status: "active",
  devices: 2,
  online: 2,
  unverified: 0,
  cameras: 1,
  cameras_online: 1,
  crit: 0,
  warn: 0,
  sensor_ok: true,
  is_primary: true,
};

const labHealth: NetworkHealth = {
  ...labRef,
  state: "warn",
  status: "no-sensor",
  devices: 1,
  online: 0,
  cameras: 0,
  cameras_online: 0,
  unverified: 1,
  crit: 0,
  warn: 1,
  sensor_ok: false,
  is_primary: false,
};

function device(overrides: Partial<DeviceCard>): DeviceCard {
  return {
    mac: "aa:bb:cc:dd:ee:01",
    ip: "192.168.1.10",
    name: "device",
    label: "",
    vendor: "",
    kind: "unknown",
    group: "unknown",
    trusted: false,
    always_on: false,
    quarantined: false,
    parent: null,
    risk: 10,
    presence: "online",
    online: true,
    first_seen: "2026-09-01T00:00:00-04:00",
    last_seen: "2026-09-09T18:00:00-04:00",
    ports: [],
    unverified: false,
    randomized: false,
    online_7d: null,
    camera_reasons: [],
    network: officeRef,
    ...overrides,
  };
}

const twoNetworkDevices: DevicesPayload = {
  groups: [
    {
      group: "computer",
      network: officeRef,
      devices: [device({ mac: "aa:bb:cc:dd:ee:01", name: "laptop-1", group: "computer" })],
    },
    {
      group: "camera",
      network: officeRef,
      devices: [device({ mac: "aa:bb:cc:dd:ee:02", name: "office-cam", group: "camera" })],
    },
    {
      group: "camera",
      network: labRef,
      devices: [
        device({
          mac: "aa:bb:cc:dd:ee:03",
          name: "lab-cam",
          group: "camera",
          ip: "10.10.0.20",
          online: false,
          unverified: true,
          network: labRef,
        }),
      ],
    },
  ],
  chips: [
    { group: "computer", count: 1, online: 1, network_id: "net_office" },
    { group: "camera", count: 1, online: 1, network_id: "net_office" },
    { group: "camera", count: 1, online: 0, network_id: "net_lab" },
  ],
  total: 3,
  online: 2,
  unverified: 1,
  cameras: 0,
  cameras_online: 0,
  tiles: NO_TILES,
  networks: [officeHealth, labHealth],
  charts: [],
};

// The multi-homed box: one physical sensor host, seen on both networks, so the
// hub sends two nodes with the same MAC. The Office node keeps the bare MAC as
// its id (the hub's convention for a device node), the Lab one is namespaced.
const SENSOR_MAC = "3c:22:fb:aa:bb:cc";

function node(overrides: Partial<TopologyPayload["nodes"][number]>): TopologyPayload["nodes"][number] {
  return {
    id: "n",
    kind: "unknown",
    group: "Unknown",
    label: "node",
    ip: "",
    mac: null,
    icon: "unknown",
    state: "online",
    risk: 0,
    ring: 2,
    trusted: true,
    quarantined: false,
    pulse: false,
    detail: "",
    network: null,
    ...overrides,
  };
}

const twoNetworkTopology: TopologyPayload = {
  networks: [officeHealth, labHealth],
  nodes: [
    node({ id: "internet", kind: "internet", group: "Internet", label: "Internet", ring: 0 }),
    node({
      id: "router:office-wi-fi",
      kind: "router",
      label: "Office router",
      ip: "192.168.1.1",
      ring: 1,
      network: officeRef,
    }),
    node({ id: "router:lab", kind: "router", label: "Lab switch", ip: "10.10.0.1", ring: 1, network: labRef }),
    node({
      id: SENSOR_MAC,
      kind: "computer",
      label: "guardian-sensor",
      ip: "192.168.1.9",
      mac: SENSOR_MAC,
      // A leaf device, same as the real hub's own payload builder would
      // send it (`payloads/topology.py` only ever hands out ring 2 to an
      // extender/switch/AP -- a "computer" is always a ring-3 leaf). The
      // map's new layered layout (`lib/topologyLayout.ts`) reads `ring` to
      // place a node's row, so this fixture has to carry the same value
      // production does rather than the generic `node()` helper's default.
      ring: 3,
      network: officeRef,
    }),
    node({
      id: `lab:${SENSOR_MAC}`,
      kind: "computer",
      label: "guardian-sensor",
      ip: "10.10.0.9",
      mac: SENSOR_MAC,
      ring: 3,
      network: labRef,
    }),
  ],
  links: [
    { source: "router:office-wi-fi", target: "internet", kind: "wan", pulse: true },
    { source: "router:lab", target: "internet", kind: "wan", pulse: true },
    { source: "router:office-wi-fi", target: SENSOR_MAC, kind: "lan", pulse: false },
    { source: "router:lab", target: `lab:${SENSOR_MAC}`, kind: "lan", pulse: false },
  ],
  // Real `cluster_by=network` clustering (`payloads/topology.py::build_clusters`)
  // files every node under its own network, including a device seen on two —
  // the lab sensor node belongs in `net_lab`'s members exactly as the office
  // one belongs in `net_office`'s.
  clusters: [
    { id: "net_office", label: "Office Wi-Fi", members: ["router:office-wi-fi", SENSOR_MAC], network_id: "net_office" },
    { id: "net_lab", label: "Lab", members: ["router:lab", `lab:${SENSOR_MAC}`], network_id: "net_lab" },
  ],
};

function guard(network: NetworkRef | null, overrides: Partial<WifiGuard> = {}): WifiGuard {
  return {
    verdict: "Wi-Fi looks safe.",
    state: "ok",
    ssid: "HomeWiFi",
    security: "WPA3",
    channel: 44,
    phy: "ax",
    signal: -45,
    evil_twin: false,
    twin_channels: [],
    rogue_dhcp: false,
    dhcp_server: "192.168.1.1",
    expected_gateway: "192.168.1.1",
    default_gateway: "",
    gateway_mac: "",
    unknown_devices: 0,
    rows: [],
    network,
    ...overrides,
  };
}

const twoNetworkSecurity: SecurityPayload = {
  protection: { defences: [], defences_host: "", blocked: [], blocked_total: 0,
                recent: [], filtering: false, days: 7, threats: [] },
  posture_coverage: { scored: 0, total: 0, unscored: [] },
  scorecards: [
    { target: "lan", kind: "lan", grade: "B", score: 84, deductions: [], fix: "" },
  ],
  wifi: guard(officeRef),
  wifis: [guard(officeRef), guard(labRef, { ssid: "Lab", expected_gateway: "10.10.0.1", default_gateway: "", gateway_mac: "", state: "warn" })],
    extra_checks: [], heartbeats: [], domains: [], email: [], ext_ports: [], fix_log: [],
  checks: [],
  attackers: [],
  exposure: [],
  attacker_method: "quarantine",
  methods: ["quarantine"],
  networks: [officeHealth, labHealth],
};

function network(overrides: Partial<Network>): Network {
  return {
    id: "net_office",
    slug: "office-wi-fi",
    name: "Office Wi-Fi",
    kind: "wifi",
    subnet_prefix: "192.168.1",
    cameras: 0,
    expected_gateway: "192.168.1.1",
    ssid: "HomeWiFi",
    sensor_id: "s1",
    sensor_name: "demo-sensor",
    colour: "",
    is_primary: true,
    status: "active",
    devices: 2,
    online: 2,
    unverified: 0,
    created: "2026-09-01T00:00:00Z",
    traffic_mode: "none",
    ...overrides,
  };
}

const twoNetworks: Network[] = [
  network({}),
  network({
    id: "net_lab",
    slug: "lab",
    name: "Lab",
    kind: "lab",
    subnet_prefix: "10.10.0",
    expected_gateway: "10.10.0.1",
    ssid: "",
    sensor_id: null,
    sensor_name: "",
    is_primary: false,
    status: "no-sensor",
    devices: 1,
    online: 0,
    unverified: 1,
  }),
];

const adminMe = {
  user: { username: "miro", display_name: "Miro", is_superadmin: false },
  tenants: [{ id: "t1", slug: "t1", name: "T1", role: "admin" }],
  active_tenant: { id: "t1", slug: "t1", name: "T1", role: "admin" },
  chat_providers: [],
};

const settingsFixture: SettingsPayload = {
  suggested_sites: [],
  profile: { owner: "", email: "", address: "", company: "" },
  users: [],
  sites: [],
  hosts: [],
  autofix: {
    enabled: true,
    max_per_day: 0,
    attacker_method: "auto",
    maintenance_window: { start: "02:00", end: "05:00" },
    cooldowns: {},
  },
  thresholds: {},
  appearance: { theme: "system", accent: "blue" },
  sensors: [
    {
      id: "s1",
      name: "demo-sensor",
      platform: "darwin",
      version: "0.1",
      status: "active",
      last_seen: "2026-09-10T08:00:00Z",
      age_s: 30,
    },
  ],
  tenants: [],
};

// --- harness ----------------------------------------------------------------

type Overrides = {
  devices?: DevicesPayload;
  topology?: TopologyPayload;
  security?: SecurityPayload;
  networks?: Network[];
  route?: string;
};

function renderPage(element: ReactElement, overrides: Overrides = {}) {
  vi.mocked(me).mockResolvedValue(adminMe);
  vi.mocked(generateEnrolCode).mockResolvedValue({
    code: "ABCD2345",
    expires: new Date(Date.now() + 10 * 60_000).toISOString(),
    sensor_id: "s1",
  });
  vi.mocked(settingsPayload).mockResolvedValue(settingsFixture);
  vi.mocked(screenTimePayload).mockResolvedValue({ days: 7, scans: 0, devices: [], heatmap: [] });

  vi.mocked(devicesPayload).mockImplementation((net?: string | null) => {
    queriedPaths.push(`/api/v1/devices${q(net)}`);
    return Promise.resolve(overrides.devices ?? twoNetworkDevices);
  });
  vi.mocked(topologyPayload).mockImplementation((clusterBy?: string, net?: string | null) => {
    queriedPaths.push(`/api/v1/topology?cluster_by=${clusterBy ?? "kind"}${q(net).replace("?", "&")}`);
    return Promise.resolve(overrides.topology ?? twoNetworkTopology);
  });
  vi.mocked(securityPayload).mockImplementation((net?: string | null) => {
    queriedPaths.push(`/api/v1/security${q(net)}`);
    return Promise.resolve(overrides.security ?? twoNetworkSecurity);
  });
  vi.mocked(networksPayload).mockImplementation(() => {
    queriedPaths.push("/api/v1/networks");
    return Promise.resolve({ networks: overrides.networks ?? twoNetworks });
  });
  vi.mocked(trafficOverview).mockResolvedValue({
    mode: "none", why_none: "", truncated: false, window_hours: 1, top_talkers: [], top_peers: [], new_peers: [], external_share: 0,
  });
  vi.mocked(deviceTraffic).mockResolvedValue({
    mode: "none", mac: "", window_hours: 24, bytes_in: 0, bytes_out: 0, peers: [], hourly: [],
  });

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[overrides.route ?? "/assets/devices"]}>
          <Routes>
            <Route path="/assets/:view" element={element} />
            <Route path="/assets/devices/:mac" element={element} />
            <Route path="/topology" element={element} />
            <Route path="/security" element={element} />
            <Route path="/settings/networks" element={element} />
            <Route path="/welcome" element={element} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  queriedPaths.length = 0;
  postedCalls.length = 0;
  try {
    // The selected network is persisted per tenant — a case that clicks the
    // Lab chip must not leave the next case scoped to the Lab.
    window.localStorage.clear();
  } catch {
    // jsdom without storage: every case then starts on "All networks" anyway.
  }
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("networks", () => {
  it("groups devices by network and scopes the query to the chosen chip", async () => {
    renderPage(<Assets />);

    expect(await screen.findByRole("heading", { name: /Office Wi-Fi/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /^Lab$/ })).toBeInTheDocument();
    expect(queried()).toContain("/api/v1/devices");

    await userEvent.click(screen.getByRole("button", { name: /^Lab,/ }));

    expect(queried()).toContain("/api/v1/devices?network=net_lab");
    // Only the Lab's section is left, and the Office's devices are gone.
    expect(await screen.findByRole("heading", { name: /^Lab$/ })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Office Wi-Fi/ })).not.toBeInTheDocument();
  });

  it("filters by kind inside one network only", async () => {
    renderPage(<Assets />);
    await screen.findByRole("heading", { name: /Office Wi-Fi/ });

    const [office, lab] = screen.getAllByTestId("network-section");
    expect(within(office).getAllByTestId("device-card")).toHaveLength(2);
    expect(within(lab).getAllByTestId("device-card")).toHaveLength(1);

    // The Office's own "Computers" chip narrows the Office grid and leaves the
    // Lab section — and the Lab's camera — exactly as it was.
    await userEvent.click(within(office).getByRole("button", { name: /Computers/ }));

    expect(within(office).getAllByTestId("device-card")).toHaveLength(1);
    expect(within(lab).getAllByTestId("device-card")).toHaveLength(1);
  });

  it("shows the needs-a-sensor badge for an unreachable network", async () => {
    renderPage(<Assets />, {
      devices: {
        ...twoNetworkDevices,
        networks: [{ ...labHealth, status: "unreachable" }],
      },
    });

    expect(await screen.findByText(/needs a sensor on this network/i)).toBeInTheDocument();
  });

  it("draws one boundary and one gateway per network, and a multi-homed device once per boundary", async () => {
    // The network map (replacing the old force-directed graph) draws each
    // network as its own physically separate place — see
    // `components/NetworkMap.tsx`'s header comment. A device seen on two
    // networks (`SENSOR_MAC` here) is drawn once in EACH boundary it is
    // reachable on, never merged into a single "on both" node the way the
    // retired `ForceGraph`/`mergeMultiHomed` did — that merge no longer
    // exists (`lib/network.ts`).
    renderPage(<Topology />, { route: "/topology" });
    await userEvent.click(await screen.findByRole("button", { name: /full organization map/i }));

    expect(await screen.findAllByTestId("map-boundary")).toHaveLength(2);

    const nodes = screen.getAllByTestId("map-node");
    expect(nodes.filter((n) => n.dataset.kind === "router")).toHaveLength(2);

    const sensor = nodes.filter((n) => n.dataset.mac === SENSOR_MAC);
    expect(sensor).toHaveLength(2);
  });

  it("scopes the topology query to the chosen network", async () => {
    renderPage(<Topology />, { route: "/topology" });
    await userEvent.click(await screen.findByRole("button", { name: /full organization map/i }));
    await screen.findAllByTestId("map-node");

    await userEvent.click(screen.getByRole("button", { name: /^Lab,/ }));

    expect(queried().some((p) => p.includes("/api/v1/topology") && p.includes("network=net_lab"))).toBe(
      true,
    );
  });

  it("shows one Wi-Fi guard panel per network", async () => {
    renderPage(<Security />, { route: "/security" });

    // `EMPTY_SECURITY` renders one network-less panel while the read is in
    // flight, so wait for a per-network heading before counting.
    expect(await screen.findByRole("heading", { name: "Lab" })).toBeInTheDocument();
    expect(screen.getAllByTestId("wifi-guard")).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "Office Wi-Fi" })).toBeInTheDocument();
  });

  it("falls back to the single primary guard when the hub sends no per-network list", async () => {
    renderPage(<Security />, {
      route: "/security",
      security: { ...twoNetworkSecurity, wifis: [], networks: [], extra_checks: [], heartbeats: [], domains: [] },
    });

    expect(await screen.findByTestId("wifi-verdict")).toBeInTheDocument();
    expect(screen.getAllByTestId("wifi-guard")).toHaveLength(1);
    expect(screen.queryByTestId("network-chips")).not.toBeInTheDocument();
  });

  it("adds a network, refuses to delete the primary, and asks where to move devices", async () => {
    renderPage(<Networks settings={settingsFixture} role="admin" />, { route: "/settings/networks" });

    await userEvent.click(await screen.findByRole("button", { name: /add network/i }));
    await userEvent.type(screen.getByLabelText(/^name$/i), "Guest Wi-Fi");
    await userEvent.selectOptions(screen.getByLabelText(/^kind$/i), "guest");
    await userEvent.type(screen.getByLabelText(/^subnet$/i), "192.168.5");
    await userEvent.type(screen.getByLabelText(/^gateway$/i), "192.168.5.1");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(posted("/api/v1/networks")).toEqual({
      name: "Guest Wi-Fi",
      kind: "guest",
      subnet_prefix: "192.168.5",
      expected_gateway: "192.168.5.1",
      ssid: "",
      colour: "",
    });

    expect(screen.getByTestId("delete-office-wi-fi")).toBeDisabled();

    await userEvent.click(screen.getByTestId("delete-lab"));
    expect(await screen.findByLabelText(/move its devices to/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /delete network/i }));
    expect(posted("/api/v1/networks/net_lab?reassign=net_office")).toEqual({});
  });

  it("refuses a gateway outside the subnet before it reaches the hub", async () => {
    renderPage(<Networks settings={settingsFixture} role="admin" />, { route: "/settings/networks" });

    await userEvent.click(await screen.findByRole("button", { name: /add network/i }));
    await userEvent.type(screen.getByLabelText(/^name$/i), "Guest Wi-Fi");
    await userEvent.type(screen.getByLabelText(/^subnet$/i), "192.168.5");
    await userEvent.type(screen.getByLabelText(/^gateway$/i), "10.0.0.1");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/inside 192\.168\.5/);
    expect(posted("/api/v1/networks")).toBeUndefined();
  });

  it("disables every write for a viewer and says why", async () => {
    renderPage(<Networks settings={settingsFixture} role="viewer" />, { route: "/settings/networks" });

    expect(await screen.findByTestId("delete-lab")).toBeDisabled();
    expect(screen.getByRole("button", { name: /add network/i })).toBeDisabled();
    expect(screen.getByText(/admin access is required/i)).toBeInTheDocument();
  });

  it("has no axe violations with two networks on screen, in either skin", async () => {
    for (const skin of ["light", "dark"] as const) {
      document.documentElement.setAttribute("data-theme", skin);
      const { container, unmount } = renderPage(<Assets />);
      await screen.findByRole("heading", { name: /Office Wi-Fi/ });

      for (const button of Array.from(container.querySelectorAll("button"))) {
        if (!button.textContent?.trim()) expect(button).toHaveAttribute("aria-label");
      }

      const results = await axe(container, axeOptions);
      // Same cast as `tests/a11y.test.tsx`: the matcher is wired in by
      // `expect.extend` above, but `@types/jest-axe` augments Jest's `expect`,
      // not Vitest's.
      (expect(results) as unknown as { toHaveNoViolations: () => void }).toHaveNoViolations();
      unmount();
    }
    document.documentElement.removeAttribute("data-theme");
  });

  it("asks which networks to watch during onboarding, before the install step", async () => {
    renderPage(<Onboarding />, { route: "/welcome", networks: [] });

    await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home{Enter}");

    expect(await screen.findByText(/which networks should Vault watch/i)).toBeInTheDocument();
    // No OS picker yet — the install step is strictly after this one.
    expect(screen.queryByRole("button", { name: /macOS/i })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));

    expect(posted("/api/v1/networks")).toBeTruthy();
    expect(await screen.findByRole("button", { name: /macOS/i })).toBeInTheDocument();
  });
});
