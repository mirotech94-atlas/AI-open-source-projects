// The accessibility pass's automated worklist: every one of the nine tabs,
// rendered with an empty (but successfully resolved) payload, in both skins,
// checked for (1) an icon-only button missing an `aria-label` and (2) any
// axe violation. `color-contrast` is disabled for axe specifically — see the
// comment below; contrast itself is verified separately and deterministically
// in `tests/contrast.test.ts`, against the real `tokens.css` values — axe
// cannot do that job here (see that file's own header comment for why).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { axe, toHaveNoViolations } from "jest-axe";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import type { MeOut } from "../src/api";

import type {
  DevicesPayload,
  DeviceTraffic,
  InboxRow,
  DnsPayload,
  HistoryPayload,
  HostsPayload,
  InboxPayload,
  OverviewPayload,
  ReportsPayload,
  ScreenTimePayload,
  SecurityPayload,
  SettingsPayload,
  SitesPayload,
  TopologyPayload,
  TrafficOverview,
  UpdatesPayload,
} from "../src/types";

// `vitest.config.ts` runs with `css: false` and jsdom has no layout engine —
// axe's `color-contrast` rule gets no computed colors/geometry under either
// condition, so it comes back "incomplete" noise rather than a real signal.
// Disabling it here is deliberate, not a workaround for a failure.
// `color-contrast` is disabled per the header comment above. `iframes: false`
// is unrelated to contrast: jsdom's `<iframe>` has no real cross-frame
// `postMessage` channel, so axe-core's own frame traversal (triggered by
// `Reports`'s live preview iframe — `ReportBuilder.tsx`) throws "Respondable
// target must be a frame in the current window" rather than skipping it —
// this option tells axe to scan only the current document, never descend.
const axeOptions = { rules: { "color-contrast": { enabled: false } }, iframes: false };

// `expect.extend` needs `toHaveNoViolations` from `jest-axe` wired in by
// hand — `globals: false` (see `vitest.config.ts`) means there is no ambient
// Jest matcher registry for it to attach to automatically, and this file is
// the only one that pays axe's import cost, not `setup.ts`.
expect.extend(toHaveNoViolations);

vi.mock("../src/lib/live", () => ({
  useLiveFeed: () => ({ status: "open" as const, last: null, on: () => () => {}, dropped: 0 }),
}));

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return { ...actual, me: vi.fn() };
});

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    overviewPayload: vi.fn(),
    livePayload: vi.fn(),
    inboxPayload: vi.fn(),
    sitesPayload: vi.fn(),
    historyPayload: vi.fn(),
    hostsPayload: vi.fn(),
    devicesPayload: vi.fn(),
    screenTimePayload: vi.fn(),
    topologyPayload: vi.fn(),
    securityPayload: vi.fn(),
    updatesPayload: vi.fn(),
    dnsPayload: vi.fn(),
    reportsPayload: vi.fn(),
    settingsPayload: vi.fn(),
    trafficOverview: vi.fn(),
    deviceTraffic: vi.fn(),
  };
});

import { me } from "../src/api";
import {
  deviceTraffic,
  devicesPayload,
  dnsPayload,
  historyPayload,
  hostsPayload,
  inboxPayload,
  livePayload,
  overviewPayload,
  reportsPayload,
  screenTimePayload,
  securityPayload,
  settingsPayload,
  sitesPayload,
  topologyPayload,
  trafficOverview,
  updatesPayload,
} from "../src/lib/api";

import Overview from "../src/pages/Overview";
import { MobileTabs } from "../src/shell/MobileTabs";
import { Sidebar } from "../src/shell/Sidebar";
import { Wallboard } from "../src/shell/Wallboard";
import Inbox from "../src/pages/Inbox";
import Assets from "../src/pages/Assets";
import History from "../src/pages/History";
import Topology from "../src/pages/Topology";
import Security from "../src/pages/Security";
import Updates from "../src/pages/Updates";
import Dns from "../src/pages/Dns";
import Reports from "../src/pages/Reports";
import Settings from "../src/pages/Settings";
import Help from "../src/pages/Help";
import ApiGuide from "../src/pages/ApiGuide";
import { NO_TILES } from "./tiles";
import { NO_HERO } from "./overviewHero";

const adminMe: MeOut = {
  user: { username: "a", display_name: "Admin", is_superadmin: false },
  tenants: [{ id: "t1", slug: "t1", name: "T1", role: "admin" }],
  active_tenant: { id: "t1", slug: "t1", name: "T1", role: "admin" },
  chat_providers: [],
};

const emptyOverview: OverviewPayload = {
  ts: "2026-09-10T08:00:00Z",
  age_s: 0,
  score: 83,
  grade: "B",
  potential: 98,
  score_model: "",
  score_coverage: null,
  verdict: "Mostly fine.",
  next_action: null,
  deductions: [],
  score_change: null,
  strip: [],
  timeline: [],
  score_trend: { name: "score", points: [], color: null },
  questions: [],
  alerts: { crit: 0, warn: 0, info: 0, open: 0, acked: 0, snoozed: 0 },
  devices: { total: 0, online: 0, sleeping: 0, unverified: 0, quarantined: 0 },
  sensor: { name: "mac", status: "active", last_seen: null, age_s: null, platform: "darwin", version: "0.1" },
  networks: [],
  chat_example_ip: "192.168.1.166",
  voice: false,
  needs_you: [],
  fixed_without_you: { days: 14, automatic: 0, approved: 0, daily: [] },
  coverage: [],
  certs_expiring: [],
  ...NO_HERO,
};

const emptyInbox: InboxPayload = { counts: { incidents: 0, alerts: 0, recs: 0, unread: 0, needs: 0 }, rows: [] };
// One chart, so axe scans a real trend panel (its series picker included) on
// the Devices and History passes rather than an empty grid (task 77).
const ONE_CHART = [{
  key: "devices_online", label: "Devices online", unit: "", max: null,
  series: [{ name: "devices online", points: [{ ts: "2026-09-08T00:00:00+00:00", value: 12 }],
             color: null }],
}];

const emptySites: SitesPayload = { sites: [], charts: ONE_CHART };
const emptyHosts: HostsPayload = { hosts: [] };
const emptyDevices: DevicesPayload = { groups: [], chips: [], total: 0, online: 0, unverified: 0, cameras: 0, cameras_online: 0, tiles: NO_TILES, networks: [], charts: ONE_CHART };
const emptyScreenTime: ScreenTimePayload = { days: 7, scans: 0, devices: [], heatmap: [] };
const emptyTopology: TopologyPayload = { nodes: [], links: [], clusters: [], networks: [] };
const emptyTraffic: TrafficOverview = {
  mode: "none", why_none: "", truncated: false, window_hours: 1, top_talkers: [], top_peers: [], new_peers: [], external_share: 0,
};
const emptyDeviceTraffic: DeviceTraffic = {
  mode: "none", mac: "", window_hours: 24, bytes_in: 0, bytes_out: 0, peers: [], hourly: [],
};
const emptyHistory: HistoryPayload = {
  days: 7, hours: 0, network: null, scan_count: 0, first_ts: null, last_ts: null,
  charts: [], scans: [], events: [],
};

const emptySecurity: SecurityPayload = {
  posture_coverage: { scored: 0, total: 0, unscored: [] },
  scorecards: [],
  wifi: {
    verdict: "",
    state: "unknown",
    ssid: "",
    security: "",
    channel: null,
    phy: "",
    signal: null,
    evil_twin: false,
    twin_channels: [],
    rogue_dhcp: false,
    dhcp_server: "",
    expected_gateway: "",
    default_gateway: "",
    gateway_mac: "",
    unknown_devices: 0,
    rows: [],
    network: null,
  },
  checks: [],
  attackers: [],
  exposure: [],
  attacker_method: "auto",
  methods: ["auto"],
  networks: [],
  wifis: [],
  extra_checks: [],
  heartbeats: [],
  domains: [],
  // Real rows, not empty lists: axe must scan the new panels' markup rather
  // than their empty states (task 77).
  email: [{ domain: "example.test", spf: "v=spf1 -all", dkim_selector: "default",
            dmarc: "v=DMARC1; p=reject;", dmarc_policy: "reject", has_mx: false, passed: true,
            checked: "2026-09-09T10:00:00-04:00", advisory: false }],
  ext_ports: [{ host: "status.example.test", ip: "203.0.113.9", ports: [22, 443, 5432],
                unexpected: [5432], findings: ["/wp-login.php"], probe_host: "vps1",
                checked: "2026-09-09T10:00:00-04:00" }],
  protection: { defences: [], defences_host: "", blocked: [], blocked_total: 0, recent: [], filtering: false, days: 7, threats: [] },
  fix_log: [{ ts: "2026-09-09T04:00:00-04:00", host: "vps1", action: "cleanup_disk",
              label: "free disk space", by: "auto", ok: true, detail: "freed 2.1G" }],
};

const emptyUpdates: UpdatesPayload = {
  calendar: [],
  // One host with packages pending, so axe scans the Servers table's Actions
  // column and the named-package panel rather than their empty states.
  hosts: [{ checked: true, name: "vps1", os: "Debian 13", kernel: "6.6.0", kernel_latest: "6.6.2", pending: 3,
            security_pending: 1, cve_fixable: 0, unattended: true, reboot_required: false,
            last_apt: "2026-09-09T04:00:00-04:00", packages: ["openssl", "curl", "libc6"],
            security_packages: ["openssl"] }],
  cves: [],
  progress: [],
  posture: { pending: 0, security_pending: 0, needs_reboot: 0, servers_checked: 0, servers_unchecked: 0, unmeasured: 0 },
  reboot_plan: { hosts: [], windows: [] },
  mac: { os: "15.6", available: ["Safari 18.6"], checked: "2026-09-09T06:00:00-04:00", error: "",
         reported: true },
};

const emptyDns: DnsPayload = {
  status: {
    enabled: false,
    running: false,
    queries_24h: 0,
    blocked_24h: 0,
    block_pct: 0,
    cache: 0,
    upstream_ms_avg: null,
    total_blocked_domains: 0,
    family_filter: false,
    uptime_s: 0,
    ts: null,
    clients_using: 0,
    devices_total: 0,
    coverage_pct: 0,
  },
  top: [],
  blocked_top: [],
  clients: [],
  rules: { allow: [], block: [] },
  lists: [],
  beaconing: [],
  suspicious: [],
  wizard: { resolver_ip: "", router_hint: "", working: false, clients_using: 0, checked_at: null },
};

const emptyReports: ReportsPayload = {
  library: [],
  presets: [],
  sections: [],
  schedule: { weekly: false, monthly: false, preset: "full", ntfy: false, hour: 8 },
      value: { days: 30, stats: [], work: [] },
};

const emptySettings: SettingsPayload = {
  suggested_sites: [],
  profile: { owner: "", email: "", address: "", company: "" },
  users: [],
  sites: [],
  hosts: [],
  autofix: { enabled: true, max_per_day: 0, attacker_method: "auto", maintenance_window: null, cooldowns: {} },
  thresholds: {},
  appearance: { theme: "system", accent: "blue" },
  sensors: [],
  tenants: [],
};

function mockEverything() {
  vi.mocked(me).mockResolvedValue(adminMe);
  vi.mocked(overviewPayload).mockResolvedValue(emptyOverview);
  vi.mocked(livePayload).mockResolvedValue({
    ts: null, sites: [], scanning: false, sensor_age_s: null, load1: null, cores: null, maintenance_until: null,
  });
  vi.mocked(historyPayload).mockResolvedValue(emptyHistory);
  vi.mocked(inboxPayload).mockResolvedValue(emptyInbox);
  vi.mocked(sitesPayload).mockResolvedValue(emptySites);
  vi.mocked(hostsPayload).mockResolvedValue(emptyHosts);
  vi.mocked(devicesPayload).mockResolvedValue(emptyDevices);
  vi.mocked(screenTimePayload).mockResolvedValue(emptyScreenTime);
  vi.mocked(topologyPayload).mockResolvedValue(emptyTopology);
  vi.mocked(trafficOverview).mockResolvedValue(emptyTraffic);
  vi.mocked(deviceTraffic).mockResolvedValue(emptyDeviceTraffic);
  vi.mocked(securityPayload).mockResolvedValue(emptySecurity);
  vi.mocked(updatesPayload).mockResolvedValue(emptyUpdates);
  vi.mocked(dnsPayload).mockResolvedValue(emptyDns);
  vi.mocked(reportsPayload).mockResolvedValue(emptyReports);
  vi.mocked(settingsPayload).mockResolvedValue(emptySettings);
}

const TABS: [string, () => ReactElement][] = [
  ["Overview", () => <Overview />],
  ["Inbox", () => <Inbox />],
  ["Assets", () => <Assets />],
  ["Topology", () => <Topology />],
  ["History", () => <History />],
  ["Security", () => <Security />],
  ["Updates", () => <Updates />],
  ["DNS", () => <Dns />],
  ["Reports", () => <Reports />],
  ["Settings", () => <Settings />],
  // The Help index, one article (the deepest markup an article produces:
  // headings, an ordered list, a pulled-out note and in-app links) and the API
  // guide. Rendered through their own routes so `useParams` gives the article
  // its slug, the way the app does it.
  ["Help", () => <Help />],
  ["Help article", () => <Help />],
  ["API guide", () => <ApiGuide />],
];

function renderTab(element: ReactElement, name = "") {
  mockEverything();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // Help is the one tab whose content depends on the route (`/help/:slug`), so
  // it is mounted under real routes rather than dropped in bare.
  const tree =
    name === "Help article" ? (
      <MemoryRouter initialEntries={["/help/alerts-and-severities"]}>
        <Routes>
          <Route path="/help/:slug" element={element} />
        </Routes>
      </MemoryRouter>
    ) : (
      <MemoryRouter initialEntries={["/overview"]}>{element}</MemoryRouter>
    );
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>{tree}</ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
  document.documentElement.removeAttribute("data-theme");
});

describe("Accessibility pass", () => {
  for (const skin of ["light", "dark"] as const) {
    describe(`in the ${skin} skin`, () => {
      it.each(TABS)("%s has no icon-only button without a label and no axe violations", async (name, make) => {
        document.documentElement.setAttribute("data-theme", skin);
        const { container } = renderTab(make(), name);

        // Let every tab's initial `useQuery` settle before inspecting the
        // DOM — otherwise every page is still on its "Loading…" branch and
        // the button/axe sweep below would be checking nothing.
        await screen.findAllByRole("heading", { level: 1 }).catch(() => []);
        await new Promise((resolve) => setTimeout(resolve, 0));

        for (const button of Array.from(container.querySelectorAll("button"))) {
          if (!button.textContent?.trim()) {
            expect(button).toHaveAttribute("aria-label");
          }
        }

        const results = await axe(container, axeOptions);
        // `@types/jest-axe` augments Jest's own global `expect`, not
        // Vitest's — and duplicating its `declare module "vitest"` pattern
        // here to type `.toHaveNoViolations()` collides with the identical
        // augmentation `@testing-library/jest-dom/vitest` already installs
        // via `tests/setup.ts` ("All declarations of 'Assertion' must have
        // identical type parameters"). The matcher itself is real —
        // `expect.extend` above wires it in at runtime — this cast only
        // silences the type-checker, which has no matching ambient type for
        // it to see.
        (expect(results) as unknown as { toHaveNoViolations: () => void }).toHaveNoViolations();
      });
    });
  }
});

// --- Task 74: the parts the empty payload above never renders ---------------
// The sweep runs every tab against an EMPTY payload, so the Overview's
// incident list, populated health panel, path rows and deduction list — and
// the sidebar's badges — would otherwise never meet axe at all.

/** The Overview with something to show in every panel added by task 74. */
const busyOverview: OverviewPayload = {
  ...emptyOverview,
  score: 83,
  grade: "B",
  potential: 98,
  verdict: "Mostly fine.",
  deductions: [{ category: "Vulnerabilities", points: 12, why: "403 fixable CVEs pending" }],
  score_trend_7d: {
    name: "score",
    points: [
      { ts: "2026-09-03T00:00:00-04:00", value: 71 },
      { ts: "2026-09-09T00:00:00-04:00", value: 83 },
    ],
    color: null,
  },
  assets: { total: 31, servers: 4, sites: 6 },
  changed_24h: 7,
  incidents: { open: 2, crit: 1, warn: 1 },
  incident_briefs: [
    {
      id: "3",
      title: "vps1: brute force from 1.2.3.4",
      sev: "CRIT",
      entity: "vps1",
      opened: "2026-09-09T12:00:00-04:00",
      alerts: 4,
      href: "/inbox?filter=incidents&row=3",
    },
  ],
  recs: { open: 3, critical: 0, high: 2, top: "Turn on unattended security upgrades" },
  health: {
    sensor_state: "online",
    run_seconds: 80,
    scans_last_hour: 10,
    sensors_active: 2,
    sensors_total: 2,
    feeds: [
      { name: "dns", ok: true },
      { name: "speed", ok: false },
    ],
    last_event_ts: "2026-09-09T18:00:00-04:00",
    last_event_age_s: 1800,
    autofix_enabled: true,
    autofix_24h: 3,
    notify_channel: "ntfy",
    dns_filter_state: "on",
    dns_blocked_24h: 412,
  },
  paths: [{ name: "cloudflare", ip: "1.1.1.1", avg_ms: 15.2, loss_pct: 0, jitter_ms: 11.5, state: "good" }],
};

const BADGED = {
  inboxUnread: 4,
  alertsOpen: 2,
  alertsCrit: 1,
  alertsWarn: 1,
  incidents: 1,
  recs: 3,
  securityCrit: 1,
  securityWarn: 0,
  updatesWarn: true,
};

/** One CRIT row for the wallboard's open-alerts list. */
const WALL_ROWS: InboxRow[] = [
  {
    id: "a1", view: "alerts", sev: "CRIT", title: "status.example.org is down", entity: "status.example.org",
    why: "", what_changed: "", status: "open", first_seen: "", last_seen: "", count: 1,
    actions: [], children: [], ticket_key: "", confidence: null, network: null, remedy: null, drift: null,
  },
];

async function expectClean(container: HTMLElement) {
  const results = await axe(container, axeOptions);
  (expect(results) as unknown as { toHaveNoViolations: () => void }).toHaveNoViolations();
}

describe("Accessibility pass — populated surfaces (task 74)", () => {
  for (const skin of ["light", "dark"] as const) {
    it(`the Overview with every panel filled has no axe violations in the ${skin} skin`, async () => {
      document.documentElement.setAttribute("data-theme", skin);
      mockEverything();
      vi.mocked(overviewPayload).mockResolvedValue(busyOverview);
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const { container } = render(
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <MemoryRouter initialEntries={["/overview"]}>
              <Overview />
            </MemoryRouter>
          </ToastProvider>
        </QueryClientProvider>,
      );
      await screen.findByRole("status", { name: "Verdict" });
      await expectClean(container);
    });

    it(`the nav carrying counts has no axe violations in the ${skin} skin`, async () => {
      document.documentElement.setAttribute("data-theme", skin);
      const { container } = render(
        <MemoryRouter initialEntries={["/inbox?filter=recs"]}>
          <Sidebar collapsed={false} onToggleCollapsed={() => {}} badges={BADGED} me={adminMe} onSwitchTenant={() => {}} />
          <MobileTabs badges={BADGED} />
        </MemoryRouter>,
      );
      // Not vacuous: the badges the scan is here for are actually in the tree.
      expect(container.querySelectorAll(".g-badge").length).toBeGreaterThan(4);
      await expectClean(container);
    });

    // Task 78: simple mode is a different tree, not the same tree with rows
    // dimmed — a reduced sidebar and a backfilled phone bar are new DOM.
    it(`the nav in simple mode has no axe violations in the ${skin} skin`, async () => {
      document.documentElement.setAttribute("data-theme", skin);
      const { container } = render(
        <MemoryRouter initialEntries={["/overview"]}>
          <Sidebar
            collapsed={false}
            onToggleCollapsed={() => {}}
            badges={BADGED}
            me={adminMe}
            onSwitchTenant={() => {}}
            advanced={false}
          />
          <MobileTabs badges={BADGED} advanced={false} />
        </MemoryRouter>,
      );
      expect(container.querySelectorAll("a[href='/dns/live']").length).toBe(0);
      await expectClean(container);
    });

    // Task 78: the wallboard, which is its own overlay and never meets the
    // per-tab sweep above.
    it(`the wallboard has no axe violations in the ${skin} skin`, async () => {
      document.documentElement.setAttribute("data-theme", skin);
      const { container } = render(
        <Wallboard
          overview={busyOverview}
          overviewFetchedAt={Date.now()}
          live={{
            ts: "2026-09-09T18:30:00-04:00",
            sites: [{ name: "status", url: "https://status.example.com", code: 200, ms: 120, ok: true, spark: [] }],
            scanning: false,
            sensor_age_s: 30,
            load1: 0.4,
            cores: 8,
            maintenance_until: null,
          }}
          rows={WALL_ROWS}
          maintenanceUntil={null}
          onClose={() => {}}
          voice
          speakOn={false}
          onToggleSpeak={() => {}}
        />,
      );
      expect(container.querySelectorAll(".g-wallboard-tile").length).toBeGreaterThan(4);
      for (const button of Array.from(container.querySelectorAll("button"))) {
        if (!button.textContent?.trim()) expect(button).toHaveAttribute("aria-label");
      }
      await expectClean(container);
    });
  }
});
