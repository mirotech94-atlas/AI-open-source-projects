import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, afterEach } from "vitest";
import type { ReactElement } from "react";
import { ToastProvider } from "../src/design";
import Topology from "../src/pages/Topology";
import type { CameraCensus, DeviceDetail, DeviceTraffic, InboxPayload, InboxRow, OverviewPayload, TopologyPayload, TrafficOverview } from "../src/types";

// --- fake live feed: Topology subscribes to "device" to invalidate its
// topology query, but no test here needs it to fire -------------------------

const { fakeLive } = vi.hoisted(() => {
  const feed = {
    status: "open" as const,
    last: null,
    on: (_type: string, _fn: (event: unknown) => void) => () => {},
  };
  return { fakeLive: feed };
});

vi.mock("../src/lib/live", () => ({
  useLiveFeed: () => fakeLive,
}));

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    topologyPayload: vi.fn(),
    overviewPayload: vi.fn(),
    securityPayload: vi.fn(),
    deviceDetail: vi.fn(),
    inboxPayload: vi.fn(),
    trafficOverview: vi.fn(),
    deviceTraffic: vi.fn(),
  };
});

// Default role is "operator" — `DeviceDrawer` mounts `useMe()` unconditionally.
vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    me: vi.fn(() =>
      Promise.resolve({
        user: { username: "myron", display_name: "Myron", is_superadmin: false },
        tenants: [{ id: "t1", slug: "t1", name: "T1", role: "operator" }],
        active_tenant: { id: "t1", slug: "t1", name: "T1", role: "operator" },
      }),
    ),
  };
});

import { deviceDetail, deviceTraffic, inboxPayload, overviewPayload, securityPayload, topologyPayload, trafficOverview } from "../src/lib/api";
import { MemoryRouter } from "react-router-dom";

afterEach(() => {
  vi.clearAllMocks();
  // The network chooser persists its pick in localStorage; without this a
  // later test mounts already narrowed, re-keys the topology query once `me`
  // lands, and remounts the map under the test's feet.
  window.localStorage.clear();
});

// --- fixtures ----------------------------------------------------------------

const officeRef = { id: "net-a", slug: "office-wifi", name: "Office Wi-Fi", colour: "" };

export const topologyFixture: TopologyPayload = {
  networks: [
    {
      id: "net-a",
      slug: "office-wifi",
      name: "Office Wi-Fi",
      colour: "",
      state: "warn",
      status: "active",
      devices: 2,
      online: 1,
      unverified: 0,
      cameras: 1,
      cameras_online: 1,
      crit: 0,
      warn: 1,
      sensor_ok: true,
      is_primary: true,
    },
    // A second network with no members below — present only so
    // `NetworkChips` (which renders nothing for fewer than two) shows up.
    {
      id: "net-b",
      slug: "lab",
      name: "Lab",
      colour: "",
      state: "good",
      status: "active",
      devices: 0,
      online: 0,
      unverified: 0,
      cameras: 0,
      cameras_online: 0,
      crit: 0,
      warn: 0,
      sensor_ok: true,
      is_primary: false,
    },
  ],
  nodes: [
    {
      id: "internet",
      kind: "internet",
      group: "Internet",
      label: "Internet",
      ip: "",
      mac: null,
      icon: "internet",
      state: "online",
      risk: 0,
      ring: 0,
      trusted: true,
      quarantined: false,
      pulse: true,
      network: null,
      detail: "Internet",
    },
    {
      id: "host:vps1",
      kind: "server",
      group: "Computers",
      label: "vps1",
      ip: "203.0.113.5",
      mac: null,
      icon: "server",
      state: "online",
      risk: 0,
      ring: 3,
      trusted: true,
      quarantined: false,
      pulse: true,
      network: null,
      detail: "vps1 · 203.0.113.5 · · risk 0",
    },
    {
      id: "aa:bb:cc:dd:ee:01",
      kind: "router",
      group: "Network gear",
      label: "Router",
      ip: "192.168.1.1",
      mac: "aa:bb:cc:dd:ee:01",
      icon: "router",
      state: "online",
      risk: 0,
      ring: 1,
      trusted: true,
      quarantined: false,
      pulse: true,
      network: officeRef,
      detail: "Router · 192.168.1.1 · · risk 0",
    },
    {
      id: "aa:bb:cc:dd:ee:02",
      kind: "camera",
      group: "Cameras",
      label: "Back door cam",
      ip: "192.168.1.42",
      mac: "aa:bb:cc:dd:ee:02",
      icon: "camera",
      state: "sleeping",
      risk: 55,
      ring: 3,
      trusted: false,
      quarantined: false,
      pulse: false,
      network: officeRef,
      detail: "Back door cam · 192.168.1.42 · Reolink · risk 55",
    },
  ],
  links: [
    { source: "aa:bb:cc:dd:ee:01", target: "internet", kind: "wan", pulse: true },
    { source: "aa:bb:cc:dd:ee:02", target: "aa:bb:cc:dd:ee:01", kind: "lan", pulse: false },
    { source: "host:vps1", target: "internet", kind: "wan", pulse: true },
  ],
  clusters: [
    { id: "Shared", label: "Shared", members: ["internet", "host:vps1"], network_id: null },
    {
      id: "Office Wi-Fi",
      label: "Office Wi-Fi",
      members: ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"],
      network_id: "net-a",
    },
  ],
};

const camerasFixture: CameraCensus = {
  known: 1,
  online: 1,
  expected: 2,
  exposed: false,
  exposed_ports: [],
  gaps: [
    {
      name: "Clinic NVR",
      expected: 2,
      known: 1,
      reason: "1 of 2 camera(s) behind it have never been listed",
      network_id: "net-a",
    },
  ],
};

const overviewFixture: OverviewPayload = {
  ts: null,
  age_s: null,
  score: null,
  grade: "",
  potential: null,
  score_model: "",
  score_coverage: null,
  verdict: "",
  next_action: null,
  deductions: [],
  score_change: null,
  strip: [],
  timeline: [],
  score_trend: { name: "score", points: [], color: null },
  questions: [],
  alerts: { open: 0, crit: 0, warn: 0, info: 0, acked: 0, snoozed: 0 },
  devices: { total: 0, online: 0, sleeping: 0, unverified: 0, quarantined: 0 },
  sensor: null,
  networks: topologyFixture.networks,
  chat_example_ip: "",
  voice: false,
  needs_you: [],
  fixed_without_you: { days: 14, automatic: 0, approved: 0, daily: [] },
  score_trend_7d: { name: "score", points: [], color: null },
  assets: { total: 0, servers: 0, sites: 0 },
  changed_24h: 0,
  incidents: { open: 0, crit: 0, warn: 0 },
  incident_briefs: [],
  recs: { open: 0, critical: 0, high: 0, top: "" },
  health: {
    sensor_state: "online",
    run_seconds: null,
    scans_last_hour: null,
    sensors_active: 1,
    sensors_total: 1,
    feeds: [],
    last_event_ts: "",
    last_event_age_s: null,
    autofix_enabled: false,
    autofix_24h: 0,
    notify_channel: "",
    dns_filter_state: "on",
    dns_blocked_24h: 0,
  },
  paths: [],
  cameras: camerasFixture,
  coverage: [],
  certs_expiring: [],
};

const deviceDetailFixture: DeviceDetail = {
  device: {
    mac: "aa:bb:cc:dd:ee:01",
    ip: "192.168.1.1",
    name: "router",
    label: "Router",
    vendor: "",
    kind: "router",
    group: "Network gear",
    trusted: true,
    always_on: true,
    quarantined: false,
    parent: null,
    risk: 0,
    presence: "online",
    online: true,
    first_seen: "2026-09-01T00:00:00-04:00",
    last_seen: "2026-09-10T00:00:00-04:00",
    ports: [],
    unverified: false,
    randomized: false,
    online_7d: null,
    camera_reasons: [],
    network: officeRef,
  },
  timeline: [],
  presence: { online_pct_24h: 100, online_pct_7d: 100, hours: [], typical_hours: [], days: [], strip: [] },
  ports: [],
  dns: { queries_24h: 0, blocked_24h: 0, top: [], blocked_top: [] },
  alerts: [],
  children: [],
  events: [],
  familiarity: { mac: "", name: "", label: "", ip: "", vendor: "", randomized_mac: false, trusted: false, verdict: "unknown", summary: "", reasons: [], days_seen: 0, lookback_days: 30, span_days: 0, first_seen: "", last_seen: "", silent_days: null, overnight_share: 0, typical_hours: [], joins: 0, leaves: 0, dns_queries: 0, top_dns: [], platform_hint: "", name_siblings: [], other_networks: [] },
};

// Empty by default -- no test here exercises the traffic panels themselves
// (that is `traffic.test.tsx`'s job); these just have to resolve so the
// drawer/map's own queries don't hang pending forever under the other
// assertions.
const trafficOverviewFixture: TrafficOverview = {
  mode: "none",
  why_none: "",
  truncated: false,
  window_hours: 1,
  top_talkers: [],
  top_peers: [],
  new_peers: [],
  external_share: 0,
};

const deviceTrafficFixture: DeviceTraffic = {
  mode: "none",
  mac: "",
  window_hours: 24,
  bytes_in: 0,
  bytes_out: 0,
  peers: [],
  hourly: [],
};

type Overrides = {
  topology?: TopologyPayload;
  overview?: OverviewPayload;
  device?: DeviceDetail;
  inbox?: InboxPayload;
};

function renderPage(element: ReactElement, overrides: Overrides = {}) {
  vi.mocked(topologyPayload).mockResolvedValue(overrides.topology ?? topologyFixture);
  vi.mocked(overviewPayload).mockResolvedValue(overrides.overview ?? overviewFixture);
  // Route check reads the Wi-Fi guard; an empty guard renders "not checked yet".
  vi.mocked(securityPayload).mockResolvedValue({ wifi: null, wifis: [] } as unknown as Awaited<ReturnType<typeof securityPayload>>);
  vi.mocked(deviceDetail).mockResolvedValue(overrides.device ?? deviceDetailFixture);
  vi.mocked(inboxPayload).mockResolvedValue(overrides.inbox ?? ({ counts: {}, rows: [] } as unknown as InboxPayload));
  vi.mocked(trafficOverview).mockResolvedValue(trafficOverviewFixture);
  vi.mocked(deviceTraffic).mockResolvedValue(deviceTrafficFixture);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // The device drawer links into the ticket desk ("Ask Vault about this
  // device"), so anything that can open it needs a router around it -- the
  // same one the real shell provides.
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>{element}</ToastProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("Topology", () => {
  it("renders one tile per hub node, split across the internet/edge/boundary bands", async () => {
    renderPage(<Topology />);
    await screen.findByRole("heading", { name: "How your network is put together" });
    const fullMap = screen.queryByRole("button", { name: "Show the full organization map" });
    if (fullMap) await userEvent.click(fullMap);
    expect(await screen.findAllByTestId("map-node")).toHaveLength(topologyFixture.nodes.length);
    expect(screen.getAllByTestId("map-boundary")).toHaveLength(1);
  });

  it("draws the unconfirmed camera as a dashed ghost slot in the same boundary as its recorder", async () => {
    renderPage(<Topology />);
    await screen.findByRole("heading", { name: "How your network is put together" });
    const fullMap = screen.queryByRole("button", { name: "Show the full organization map" });
    if (fullMap) await userEvent.click(fullMap);
    const ghosts = await screen.findAllByTestId("map-ghost");
    expect(ghosts).toHaveLength(1);
    expect(ghosts[0]).toHaveAccessibleName(/never been? listed|never listed|behind Clinic NVR/i);
  });

  it("opens the device drawer on click, keeping role-scoped device actions", async () => {
    renderPage(<Topology />);
    await screen.findByRole("heading", { name: "How your network is put together" });
    const fullMap = screen.queryByRole("button", { name: "Show the full organization map" });
    if (fullMap) await userEvent.click(fullMap);
    const nodes = await screen.findAllByTestId("map-node");
    const router = nodes.find((n) => n.getAttribute("data-mac") === "aa:bb:cc:dd:ee:01");
    expect(router).toBeDefined();
    await userEvent.click(router!);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("opens a facts drawer for a non-device node (no mac)", async () => {
    renderPage(<Topology />);
    await screen.findByRole("heading", { name: "How your network is put together" });
    const fullMap = screen.queryByRole("button", { name: "Show the full organization map" });
    if (fullMap) await userEvent.click(fullMap);
    const nodes = await screen.findAllByTestId("map-node");
    const internet = nodes.find((n) => n.getAttribute("data-mac") === "");
    expect(internet).toBeDefined();
    await userEvent.click(internet!);
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Internet");
  });

  it("opens the gap's own explanation, never a device drawer, for a ghost slot", async () => {
    renderPage(<Topology />);
    await screen.findByRole("heading", { name: "How your network is put together" });
    const fullMap = screen.queryByRole("button", { name: "Show the full organization map" });
    if (fullMap) await userEvent.click(fullMap);
    const ghost = (await screen.findAllByTestId("map-ghost"))[0];
    await userEvent.click(ghost);
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Camera, never listed");
    expect(dialog).toHaveTextContent(/never been listed/);
  });

  it("keyboard-activates a tile with Enter, same as a click", async () => {
    renderPage(<Topology />);
    await screen.findByRole("heading", { name: "How your network is put together" });
    const fullMap = screen.queryByRole("button", { name: "Show the full organization map" });
    if (fullMap) await userEvent.click(fullMap);
    const nodes = await screen.findAllByTestId("map-node");
    const internet = nodes.find((n) => n.getAttribute("data-mac") === "")!;
    internet.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("narrows the map to one network via the network chooser", async () => {
    renderPage(<Topology />);
    await screen.findByRole("heading", { name: "How your network is put together" });
    const fullMap = screen.queryByRole("button", { name: "Show the full organization map" });
    if (fullMap) await userEvent.click(fullMap);
    await screen.findAllByTestId("map-node");
    await userEvent.click(screen.getByRole("button", { name: /Lab/ }));
    expect(topologyPayload).toHaveBeenCalledWith("network", "net-b");
  });

  it("colours a link by the node it leads to and opens that node when a coloured link is clicked", async () => {
    // A host/site node: its details open in the synchronous facts drawer, so
    // the assertion is about the link -> node click-through, not a data fetch.
    const leaf = topologyFixture.nodes.find((n) => n.mac === null && n.kind !== "internet")!;
    const alertKey = `sec:panel:${leaf.label}:/admin`;
    const topology = {
      ...topologyFixture,
      nodes: topologyFixture.nodes.map((n) => (n.id === leaf.id ? { ...n, alert_sev: "CRIT", alert_keys: [alertKey] } : n)),
    };
    const row = {
      id: alertKey, view: "alerts", sev: "CRIT", title: "Admin panel reachable from the internet",
      entity: leaf.label, why: "", what_changed: "", status: "open", first_seen: "", last_seen: "", count: 1,
      actions: [{ kind: "fix", label: "Fix", method: "POST", path: "/api/v1/fix", body: { host: leaf.label, action: "close_panel", arg: null }, tier: 2 }],
      children: [], ticket_key: "", confidence: null, network: null, remedy: null, drift: null,
    } as unknown as InboxRow;
    renderPage(<Topology />, { topology, inbox: { counts: {}, rows: [row] } as unknown as InboxPayload });
    // queryByRole is synchronous: without waiting for the page first it runs
    // before anything has rendered, finds nothing, and leaves the map behind
    // the network chooser.
    await screen.findByRole("heading", { name: "How your network is put together" });
    const fm = screen.queryByRole("button", { name: "Show the full organization map" });
    if (fm) await userEvent.click(fm);
    const links = await screen.findAllByTestId("map-link");
    const red = links.find((l) => l.getAttribute("data-tone") === "alert");
    expect(red).toBeDefined();
    expect(red!.getAttribute("role")).toBe("button");           // red links are clickable
    expect(links.some((l) => l.getAttribute("data-tone") === "good")).toBe(true);
    expect(red!.getAttribute("data-leaf")).toBe(leaf.id);          // it leads to the flagged node
    // fireEvent, not userEvent: user-event's pointer simulation cannot click an
    // SVG <g> in jsdom, but the handler is a plain React onClick.
    fireEvent.click(red!);
    const dialog = await screen.findByRole("dialog");                  // that node's issue drawer opened
    expect(dialog).toHaveTextContent(leaf.label);
    expect(await within(dialog).findByText(/Admin panel reachable/)).toBeInTheDocument(); // the open alert is shown ...
    expect(await within(dialog).findByRole("button", { name: /^Fix/ })).toBeInTheDocument(); // ... with its Fix button
  });

  it("opens the confidence working on an alert inside the link drawer", async () => {
    // `LinkIssueDrawer` renders `InboxRowView`, which takes its expanded state
    // from whatever parent mounts it. This one used to pass a constant `false`
    // and a no-op toggle -- correct while the only expandable row was an
    // incident header, which never appears here, and a dead control the moment
    // an alert row had a drawer of its own. No test file names this component,
    // so the control could go back to doing nothing without a failure
    // anywhere; this is the map's side of that guard.
    const leaf = topologyFixture.nodes.find((n) => n.mac === null && n.kind !== "internet")!;
    const alertKey = `sec:panel:${leaf.label}:/admin`;
    const topology = {
      ...topologyFixture,
      nodes: topologyFixture.nodes.map((n) => (n.id === leaf.id ? { ...n, alert_sev: "CRIT", alert_keys: [alertKey] } : n)),
    };
    const row = {
      id: alertKey, view: "alerts", sev: "CRIT", title: "Admin panel reachable from the internet",
      entity: leaf.label, why: "", what_changed: "", status: "open", first_seen: "", last_seen: "", count: 1,
      actions: [], children: [], ticket_key: "", confidence: null, network: null, drift: null,
      remedy: {
        kind: "approval", action: "close_panel", host: leaf.label, arg: "", tier: 2,
        tool: "apply_fix", min_role: "admin", can_run_now: false,
        summary: `I can repair this by closing the panel on ${leaf.label}.`,
        need: "An admin has to run it.", impact: "", opening: "",
        confidence: 55,
        confidence_why: "Guardian has never repaired this host this way.",
        confidence_factors: [
          { name: "host_history", question: `Has Guardian run close panel on ${leaf.label} successfully before?`, answer: "never on this host", weight: -20 },
        ],
      },
    } as unknown as InboxRow;
    renderPage(<Topology />, { topology, inbox: { counts: {}, rows: [row] } as unknown as InboxPayload });
    await screen.findByRole("heading", { name: "How your network is put together" });
    const fm = screen.queryByRole("button", { name: "Show the full organization map" });
    if (fm) await userEvent.click(fm);
    const links = await screen.findAllByTestId("map-link");
    fireEvent.click(links.find((l) => l.getAttribute("data-tone") === "alert")!);

    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByTestId("inbox-confidence-why")).toHaveTextContent(
      "never repaired this host this way",
    );
    expect(within(dialog).queryByTestId("inbox-score")).toBeNull();

    await userEvent.click(within(dialog).getByTestId("inbox-score-toggle"));

    expect(within(dialog).getByTestId("inbox-score-value")).toHaveTextContent("55 / 100");
    expect(within(dialog).getByText("never on this host")).toBeInTheDocument();
  });

  it("names why a link is red even when no alert row exists (offline, untrusted)", async () => {
    const leaf = topologyFixture.nodes.find((n) => n.mac !== null && n.kind !== "router")!;
    const topology = {
      ...topologyFixture,
      nodes: topologyFixture.nodes.map((n) =>
        n.id === leaf.id ? { ...n, state: "offline", trusted: false, alert_sev: null, alert_keys: [] } : n,
      ),
    };
    renderPage(<Topology />, { topology });
    // queryByRole is synchronous: without waiting for the page first it runs
    // before anything has rendered, finds nothing, and leaves the map behind
    // the network chooser.
    await screen.findByRole("heading", { name: "How your network is put together" });
    const fm = screen.queryByRole("button", { name: "Show the full organization map" });
    if (fm) await userEvent.click(fm);
    const links = await screen.findAllByTestId("map-link");
    const red = links.find((l) => l.getAttribute("data-leaf") === leaf.id)!;
    expect(red.getAttribute("data-tone")).toBe("alert");
    fireEvent.click(red);
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("A red link means");
    expect(within(dialog).getByText(/Offline — it has not answered/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Not marked as yours yet/)).toBeInTheDocument();
    expect(await within(dialog).findByTestId("link-issue-empty")).toBeInTheDocument();
  });
});
