// The sections restored in task 75: each one existed in the owner's reference
// console and drew data the web app had already fetched and never rendered.
// These render the components directly — the pages that host them are covered
// by their own suites — so a column silently dropped again fails here.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { HostsTable, SitesTable } from "../src/components/AssetTables";
import { DnsInsights, DnsOddLookups } from "../src/components/DnsInsights";
import { DevicesTable } from "../src/components/DevicesTable";
import { TrendPanels } from "../src/components/TrendPanels";
import { UpdateHosts } from "../src/components/UpdateHosts";
import { searchDevices } from "../src/lib/network";
import type { DeviceCard, DevicesPayload, HostCard, SiteCard, UpdateHost } from "../src/types";

const UPDATE_HOSTS: UpdateHost[] = [
  {
    checked: true,
    name: "vps1",
    os: "Debian 13",
    kernel: "6.6.0",
    kernel_latest: "6.6.2",
    pending: 4,
    security_pending: 1,
    cve_fixable: 2,
    unattended: true,
    reboot_required: true,
    last_apt: "2026-09-09T04:00:00-04:00",
    packages: [],
    security_packages: [],
  },
  {
    checked: true,
    name: "mac",
    os: "macOS 15",
    kernel: "",
    kernel_latest: "",
    pending: 0,
    security_pending: 0,
    cve_fixable: 0,
    unattended: null,
    reboot_required: false,
    last_apt: "",
    packages: [],
    security_packages: [],
  },
];

const SITE: SiteCard = {
  name: "status",
  url: "https://status.example.com",
  ok: true,
  code: 200,
  ms: 120,
  uptime_24h: 100,
  uptime_7d: 99.9,
  uptime_24h_checks: 288,
  uptime_7d_checks: 2016,
  uptime_24h_planned: 0,
  uptime_7d_planned: 0,
  latency_spark: [100, 140],
  cert_days: 60,
  cert_expires: "2026-11-01",
  cert_issuer: "Let's Encrypt",
  dns_a: ["1.2.3.4", "5.6.7.8"],
  headers: { hsts: true, nosniff: true, frame: true, csp: true, https_redirect: true, old_tls: false, server: "" },
  incidents_7d: 2,
  public: true,
  stale: false,
  stale_note: "",
  last_checked: "",
};

const HOST: HostCard = {
  name: "vps1",
  address: "203.0.113.9",
  reachable: true,
  hostname: "vps1.example.com",
  uptime: "31 days",
  load1: 0.5,
  load_per_core: 0.25,
  disk_pct: 61,
  mem_pct: 44,
  failed_ssh_24h: 812,
  banned: 7,
  fail2ban: true,
  reboot_required: false,
  pending_updates: 4,
  security_updates: 1,
  cve_fixable: 2,
  hardening: { score: 80, grade: "B", checks: [] },
  ports: [{ port: 22, since: "", risky: false, service: "ssh" }],
  docker: [{ name: "nginx", status: "running", image: "nginx" }],
  services: [{ name: "sshd", state: "active" }],
  gauges: [],
  stale: false,
  stale_note: "",
  last_checked: "",
};

function device(over: Partial<DeviceCard>): DeviceCard {
  return {
    mac: "aa:bb:cc:dd:ee:01",
    ip: "192.168.1.10",
    name: "thing",
    label: "",
    vendor: "Apple",
    kind: "computer",
    group: "computer",
    trusted: true,
    always_on: false,
    quarantined: false,
    parent: null,
    risk: 10,
    presence: "ONLINE",
    online: true,
    first_seen: "",
    last_seen: "",
    ports: [],
    unverified: false,
    randomized: false,
    network: null,
    online_7d: null,
    camera_reasons: [],
    ...over,
  };
}

const DEVICES: DevicesPayload = {
  groups: [
    {
      group: "computer",
      network: null,
      devices: [
        device({ mac: "aa:bb:cc:dd:ee:01", name: "Studio", vendor: "Apple" }),
        device({ mac: "aa:bb:cc:dd:ee:02", name: "Driveway", vendor: "Reolink", ip: "192.168.1.51", online: false }),
      ],
    },
  ],
  chips: [{ group: "computer", count: 2, online: 1, network_id: null }],
  total: 2,
  online: 1,
  unverified: 0,
  cameras: 0,
  cameras_online: 0,
  tiles: {
    new_devices: 0, trusted: 0, randomized: 0, down_mbps: null, up_mbps: null,
    latency_ms: null, dns_ms: null, dns_ok: null, wan_ip: "", wan_ips: [],
  },
  networks: [],
  charts: [],
};

describe("Updates — the reference's per-server table", () => {
  it("draws every column, including the four fields nothing else rendered", () => {
    render(<UpdateHosts hosts={UPDATE_HOSTS} />);
    // "Server", not "Host": item 22 -- they are the same thing, and the
    // product uses one word for it.
    for (const header of ["Server", "OS", "Kernel", "Pending", "Security", "Fixable CVEs", "Auto-updates", "Last apt run", "Reboot"]) {
      expect(screen.getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    // The newer installed kernel is named beside the running one.
    expect(screen.getByText(/6\.6\.2/)).toBeInTheDocument();
    expect(screen.getByText("Debian 13")).toBeInTheDocument();
  });

  it("says nothing rather than 'off' when auto-updates were never reported", () => {
    render(<UpdateHosts hosts={UPDATE_HOSTS} />);
    // One host reports `true`, the other `null`: exactly one "on", no "off".
    expect(screen.getByText("on")).toBeInTheDocument();
    expect(screen.queryByText("off")).toBeNull();
  });
});

describe("Assets — the websites and servers detail tables", () => {
  it("renders the site fields that had no home: issuer, expiry, DNS, incidents", () => {
    render(<SitesTable sites={[SITE]} certWarnDays={14} />);
    expect(screen.getByText("Let's Encrypt")).toBeInTheDocument();
    expect(screen.getByText("2026-11-01")).toBeInTheDocument();
    expect(screen.getByText("1.2.3.4")).toBeInTheDocument();
    expect(screen.getByText("+1")).toBeInTheDocument(); // the second A record
    expect(screen.getByText("200")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument(); // incidents in 7 days
  });

  it("renders the host fields that had no home: uptime, load/core, fail2ban, banned, failed SSH", () => {
    render(<HostsTable hosts={[HOST]} />);
    expect(screen.getByText("31 days")).toBeInTheDocument();
    expect(screen.getByText("0.25")).toBeInTheDocument();
    expect(screen.getByText("on")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("812")).toBeInTheDocument();
  });
});

describe("DNS — the three ranked lists and the odd-lookup table", () => {
  it("draws every list from the payload fields the page already fetched", () => {
    render(
      <DnsInsights
        top={[{ name: "example.com", count: 900 }]}
        blockedTop={[{ name: "ads.example.com", count: 40 }]}
        clients={[{ mac: "aa:bb", ip: "192.168.1.20", name: "Windows PC", queries: 500, blocked: 12, network: null }]}
      />,
    );
    expect(screen.getByText("example.com")).toBeInTheDocument();
    expect(screen.getByText("ads.example.com")).toBeInTheDocument();
    expect(screen.getByText("Windows PC")).toBeInTheDocument();
    expect(screen.getByTestId("dns-blocked")).toBeInTheDocument();
  });

  it("names the pattern a row came from, and says so when there is nothing", () => {
    const { unmount } = render(
      <DnsOddLookups
        payload={{ beaconing: [{ name: "telemetry.example.com", count: 12 }], suspicious: [{ name: "x9f2k.example", count: 3 }] }}
      />,
    );
    expect(screen.getByText("fixed timer")).toBeInTheDocument();
    expect(screen.getByText("random-looking")).toBeInTheDocument();
    unmount();

    render(<DnsOddLookups payload={{ beaconing: [], suspicious: [] }} />);
    expect(screen.getByText(/nothing unusual/i)).toBeInTheDocument();
  });
});

describe("Devices — the reference's search box", () => {
  it("matches on name, IP, MAC and vendor, and recounts the chips", () => {
    expect(searchDevices(DEVICES, "reolink").total).toBe(1);
    expect(searchDevices(DEVICES, "192.168.1.51").total).toBe(1);
    expect(searchDevices(DEVICES, "ee:01").total).toBe(1);
    expect(searchDevices(DEVICES, "studio").chips[0]?.count).toBe(1);
    // A chip whose devices all filtered out disappears rather than claiming 0.
    expect(searchDevices(DEVICES, "nothing-matches").chips).toEqual([]);
  });

  it("returns the payload untouched for an empty query", () => {
    expect(searchDevices(DEVICES, "   ")).toBe(DEVICES);
  });

  it("counts online only among what survives the search", async () => {
    const filtered = searchDevices(DEVICES, "driveway");
    expect(filtered.online).toBe(0);
    // And the control itself is a real search input, keyboard-reachable.
    render(
      <label>
        <span>Search devices</span>
        <input type="search" />
      </label>,
    );
    await userEvent.type(screen.getByRole("searchbox"), "cam");
    expect(screen.getByRole("searchbox")).toHaveValue("cam");
  });
});

// --- task 77 -----------------------------------------------------------------

describe("Websites — the reference's three trend panels", () => {
  const CHARTS = [
    {
      key: "site_latency",
      label: "Latency history",
      unit: "ms",
      max: null,
      series: [
        { name: "status", points: [{ ts: "2026-09-08T00:00:00+00:00", value: 120 }], color: null },
        { name: "docs", points: [{ ts: "2026-09-08T00:00:00+00:00", value: 90 }], color: null },
        { name: "shop", points: [{ ts: "2026-09-08T00:00:00+00:00", value: 210 }], color: null },
      ],
    },
    {
      key: "site_cert",
      label: "Certificate days left",
      unit: "days",
      max: null,
      series: [{ name: "status", points: [{ ts: "2026-09-08T00:00:00+00:00", value: 60 }], color: null }],
    },
    {
      key: "site_uptime",
      label: "Availability",
      unit: "%",
      max: 100,
      series: [{ name: "status", points: [{ ts: "2026-09-08T00:00:00+00:00", value: 100 }], color: null }],
    },
  ];

  it("draws one panel per hub chart, in the hub's order", () => {
    render(<TrendPanels charts={CHARTS} />);
    for (const label of ["Latency history", "Certificate days left", "Availability"]) {
      expect(screen.getByRole("heading", { name: label })).toBeInTheDocument();
    }
  });

  it("offers the series picker only where a panel has more series than a chart draws", () => {
    render(<TrendPanels charts={CHARTS} />);
    expect(screen.getByLabelText("Series shown in Latency history")).toBeInTheDocument();
    expect(screen.queryByLabelText("Series shown in Availability")).not.toBeInTheDocument();
  });

  it("draws nothing at all when the hub sent no chart", () => {
    const { container } = render(<TrendPanels charts={[]} label="How each site has been doing" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("Devices — the reference's table view", () => {
  const rows = DEVICES.groups.flatMap((g) => g.devices);

  it("carries the reference's columns in order", () => {
    render(<DevicesTable devices={rows} onOpen={() => {}} />);
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual([
      "Kind",
      "IP",
      "Name",
      "State",
      "Risk",
      "Services",
      "MAC",
      "Vendor",
      "Label",
      "Online 7d",
      "First seen",
      "Last seen",
      "Network",
    ]);
  });

  it("prints the hub's own online-7d figure, and an em dash when nothing measured it", () => {
    render(
      <DevicesTable
        devices={[
          { ...rows[0], online_7d: 92.5 },
          { ...rows[1], mac: "aa:bb:cc:00:00:01", online_7d: null },
        ]}
        onOpen={() => {}}
      />,
    );
    expect(screen.getByText("92.5%")).toBeInTheDocument();
  });

  it("opens a device from its row", async () => {
    const opened: string[] = [];
    render(<DevicesTable devices={rows} onOpen={(mac) => opened.push(mac)} />);
    await userEvent.click(screen.getByText(rows[0].mac));
    expect(opened).toEqual([rows[0].mac]);
  });
});

describe("a server the sweep never reached (item 30)", () => {
  it("is listed, flagged, and shows unknown rather than a tidy row of zeros", () => {
    // It used to be absent from the table entirely, and absent reads as
    // fine. An unpatched machine nobody is looking at is the one that
    // gets used.
    render(
      <UpdateHosts
        hosts={[{
          checked: false, name: "forgotten", os: "", kernel: "", kernel_latest: "",
          pending: 0, security_pending: 0, cve_fixable: 0, unattended: null,
          reboot_required: false, last_apt: "", packages: [], security_packages: [],
        }]}
      />,
    );
    const row = screen.getByText("forgotten").closest("tr");
    expect(row).not.toBeNull();
    expect(row).toHaveTextContent(/not checked/i);
    expect(row).toHaveTextContent(/unknown/i);
    // The one thing it must never say is "0 pending", which would read as
    // "nothing to install here".
    expect(row).not.toHaveTextContent(/nothing pending/i);
  });
});
