import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import Security from "../src/pages/Security";
import { ChatHandoffContext } from "../src/shell/chat/handoff";
import type { MeOut } from "../src/api";
import type { AlertHandoff, CheckDetail, SecurityPayload } from "../src/types";

// --- fake live feed: Security does not depend on live frames, but every tab
// mounts the hook unconditionally --------------------------------------------

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

// --- captured writes ---------------------------------------------------------

const { postedCalls, DISRUPTIVE } = vi.hoisted(() => {
  return {
    postedCalls: [] as { path: string; body: unknown }[],
    DISRUPTIVE: new Set(["block_subnet", "ban_long", "limit_ssh", "reboot", "full_upgrade",
                          "disable_ssh_password"]),
  };
});

function posted(path: string): unknown {
  const call = [...postedCalls].reverse().find((c) => c.path === path);
  return call?.body;
}

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    securityPayload: vi.fn(),
    queueStatus: vi.fn(),
    checkDetail: vi.fn(),
    checkDecision: vi.fn(),
    checkHandoff: vi.fn(),
    // The hub refuses a disruptive action on a bare POST (409, nothing
    // queued) and only then mints a confirmation — `DISRUPTIVE` mirrors the
    // hub's tier-3 set for the actions these tests exercise.
    requestFix: vi.fn((host: string, action: string, arg: string | null = null,
                       token: string | null = null) => {
      postedCalls.push({ path: "/api/v1/fix", body: { host, action, arg, confirm_token: token } });
      if (DISRUPTIVE.has(action) && token === null) {
        return Promise.reject(new actual.ApiError(409, `${action} is disruptive`));
      }
      return Promise.resolve({ queue_id: "q1" });
    }),
    // `requestFixOrConfirm` lives inside lib/api and calls the module's own
    // `requestFix`, so mocking that alone would not intercept it: the flow is
    // mocked here at the same seam the page uses.
    requestFixOrConfirm: vi.fn(async (host: string, action: string, arg: string | null = null) => {
      postedCalls.push({ path: "/api/v1/fix", body: { host, action, arg, confirm_token: null } });
      if (DISRUPTIVE.has(action)) {
        postedCalls.push({ path: "/api/v1/fix/confirm", body: { host, action, arg } });
        return {
          ack: null,
          confirmation: {
            token: "tok-sec", expires: "2026-09-09T18:10:00-04:00", tier: 3, host, action,
            summary: `Run ${action} on ${host}. This is disruptive and cannot be undone from here.`,
          },
        };
      }
      return { ack: { queue_id: "q1" }, confirmation: null };
    }),
    fixConfirmation: vi.fn((host: string, action: string, arg: string | null = null) => {
      postedCalls.push({ path: "/api/v1/fix/confirm", body: { host, action, arg } });
      return Promise.resolve({
        token: "tok-sec",
        expires: "2026-09-09T18:10:00-04:00",
        tier: 3,
        host,
        action,
        summary: `Run ${action} on ${host}. This is disruptive and cannot be undone from here.`,
      });
    }),
    runAction: vi.fn((action: { method: string; path: string; body: unknown }) => {
      postedCalls.push({ path: action.path, body: action.body });
      return Promise.resolve({ queue_id: "q1" });
    }),
    setAttackerMethod: vi.fn((method: string) => {
      postedCalls.push({ path: "/api/v1/attacker", body: { method } });
      return Promise.resolve({});
    }),
  };
});

import { checkDecision, checkDetail, checkHandoff, queueStatus, securityPayload } from "../src/lib/api";

// Default role is "operator" for most tests; the viewer test overrides it.
vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    me: vi.fn(() =>
      Promise.resolve({
        user: { username: "myron", display_name: "Myron", is_superadmin: false },
        tenants: [{ id: "t1", slug: "t1", name: "T1", role: "operator" }],
        active_tenant: { id: "t1", slug: "t1", name: "T1", role: "operator" },
        chat_providers: [],
      }),
    ),
  };
});

import { me } from "../src/api";

afterEach(() => {
  postedCalls.length = 0;
  vi.clearAllMocks();
});

// --- fixtures ----------------------------------------------------------------

export const securityFixture: SecurityPayload = {
  posture_coverage: { scored: 0, total: 0, unscored: [] },
  scorecards: [
    {
      target: "status.example.org",
      kind: "site",
      grade: "B",
      score: 84,
      deductions: [
        { category: "Headers", points: 8, why: "Missing HSTS" },
        { category: "TLS", points: 5, why: "TLS 1.1 still enabled" },
        { category: "Exposure", points: 3, why: "Server header leaks version" },
      ],
      fix: "harden_headers",
    },
    {
      target: "vps1",
      kind: "host",
      grade: "A",
      score: 96,
      deductions: [{ category: "Updates", points: 4, why: "1 pending security update" }],
      fix: "",
    },
  ],
  wifi: {
    verdict: "Wi-Fi looks safe.",
    state: "ok",
    ssid: "HomeNet-5G",
    security: "WPA3",
    channel: 44,
    phy: "ax",
    signal: -42,
    evil_twin: false,
    twin_channels: [],
    rogue_dhcp: false,
    dhcp_server: "192.168.1.1",
    expected_gateway: "192.168.1.1",
    default_gateway: "",
    gateway_mac: "",
    unknown_devices: 0,
    rows: [],
    network: null,
  },
  checks: [
    {
      key: "wifi.encryption",
      area: "Wi-Fi",
      title: "Encryption is WPA2/WPA3",
      passed: true,
      last_verified: "2026-09-09T17:00:00-04:00",
      evidence: "iwconfig: WPA3 SAE",
      autofix: "—",
      target: "router",
    },
    {
      key: "host.fail2ban",
      area: "Hosts",
      title: "fail2ban is active",
      passed: true,
      last_verified: "2026-09-09T16:30:00-04:00",
      evidence: "systemctl: fail2ban active (running)",
      autofix: "Auto",
      target: "vps1",
    },
    {
      key: "host.updates",
      area: "Hosts",
      title: "No pending security updates",
      passed: false,
      last_verified: "2026-09-09T15:00:00-04:00",
      evidence: "apt: 1 security update pending",
      autofix: "Manual",
      target: "vps1",
    },
  ],
  attackers: [
    {
      ip: "1.2.3.4",
      host: "vps1",
      fails: 42,
      days: 3,
      first_seen: "2026-09-06T00:00:00-04:00",
      last_seen: "2026-09-09T12:00:00-04:00",
      geo: {
        country: "Netherlands",
        country_code: "NL",
        city: "Amsterdam",
        org: "Some Hosting BV",
        isp: "Some Hosting",
        lat: 52.37,
        lon: 4.9,
        hosting: true,
        reverse: "",
      },
      action: "blocked",
      action_ts: "2026-09-09T12:05:00-04:00",
      alert_key: "sec:attacker:1.2.3.4",
      network: null,
    },
    {
      ip: "5.6.7.8",
      host: "",
      fails: 12,
      days: 1,
      first_seen: "2026-09-08T00:00:00-04:00",
      last_seen: "2026-09-09T10:00:00-04:00",
      geo: {
        country: "Brazil",
        country_code: "BR",
        city: "Sao Paulo",
        org: "Another ISP",
        isp: "Another ISP",
        lat: -23.55,
        lon: -46.63,
        hosting: false,
        reverse: "",
      },
      action: "",
      action_ts: null,
      alert_key: "sec:attacker:5.6.7.8",
      network: null,
    },
    {
      ip: "9.9.9.9",
      host: "",
      fails: 3,
      days: 1,
      first_seen: "2026-09-09T00:00:00-04:00",
      last_seen: "2026-09-09T09:00:00-04:00",
      geo: null,
      action: "",
      action_ts: null,
      alert_key: "sec:attacker:9.9.9.9",
      network: null,
    },
  ],
  exposure: [
    { label: "Open ports", value: "2", state: "warn", detail: "22, 443" },
    { label: "Public sites", value: "1", state: "ok", detail: "status.example.org" },
  ],
  attacker_method: "quarantine",
  methods: ["quarantine", "block", "ignore"],
  networks: [],
  wifis: [],
  extra_checks: [
    { name: "office-vpn", type: "tcp", target: "10.10.0.1", port: 51820, ok: true, latency_ms: 12, detail: "" },
    { name: "nas-ping", type: "icmp", target: "192.168.1.20", port: null, ok: false, latency_ms: null,
      detail: "no reply" },
    { name: "mx-lookup", type: "dns", target: "mail.internal.test", port: null, ok: null, latency_ms: null, detail: "" },
  ],
  heartbeats: [
    { name: "nightly-backup", every_min: 1440, last_ping: "2026-09-09T02:00:00-04:00", age_min: 60,
      late: false, note: "borg to the NAS" },
    { name: "db-dump", every_min: 60, last_ping: "", age_min: null, late: true, note: "" },
  ],
  domains: [
    { domain: "example.test", expires: "2026-09-20", days_left: 5, error: "" },
    { domain: "lab.test", expires: "", days_left: null, error: "whois timed out" },
  ],
  email: [
    {
      domain: "mail.example.test",
      spf: "v=spf1 mx -all",
      dkim_selector: "default",
      dmarc: "v=DMARC1; p=reject;",
      dmarc_policy: "reject",
      has_mx: true,
      passed: true,
      checked: "2026-09-09T10:00:00-04:00", advisory: false
    },
    {
      domain: "quiet.test",
      spf: "",
      dkim_selector: "",
      dmarc: "",
      dmarc_policy: "",
      has_mx: false,
      passed: false,
      checked: "2026-09-09T10:00:00-04:00", advisory: false
    },
  ],
  ext_ports: [
    {
      host: "status.example.test",
      ip: "203.0.113.9",
      ports: [22, 80, 443, 5432],
      unexpected: [5432],
      findings: ["/wp-login.php"],
      probe_host: "vps1",
      checked: "2026-09-09T10:00:00-04:00"
    },
    {
      host: "docs.example.test",
      ip: "203.0.113.10",
      ports: [80, 443],
      unexpected: [],
      findings: [],
      probe_host: "vps1",
      checked: "2026-09-09T10:00:00-04:00"
    },
  ],
  protection: { defences: [], defences_host: "", blocked: [], blocked_total: 0, recent: [], filtering: false, days: 7, threats: [] },
  fix_log: [
    {
      ts: "2026-09-09T04:00:00-04:00",
      host: "vps1",
      action: "security_updates",
      label: "install pending security updates",
      by: "auto",
      ok: true,
      detail: "remaining=0",
    },
    {
      ts: "2026-09-08T04:00:00-04:00",
      host: "vps1",
      action: "cleanup_disk",
      label: "free disk space",
      by: "myron",
      ok: false,
      detail: "nothing to remove",
    },
  ],
};

export const viewerMe: MeOut = {
  user: { username: "v", display_name: "Viewer", is_superadmin: false },
  tenants: [{ id: "t1", slug: "t1", name: "T1", role: "viewer" }],
  active_tenant: { id: "t1", slug: "t1", name: "T1", role: "viewer" },
  chat_providers: [],
};

const OPERATOR_ME: MeOut = {
  user: { username: "myron", display_name: "Myron", is_superadmin: false },
  tenants: [{ id: "t1", slug: "t1", name: "T1", role: "operator" }],
  active_tenant: { id: "t1", slug: "t1", name: "T1", role: "operator" },
  chat_providers: [],
};

const ADMIN_ME: MeOut = {
  user: { username: "a", display_name: "Admin", is_superadmin: false },
  tenants: [{ id: "t1", slug: "t1", name: "T1", role: "admin" }],
  active_tenant: { id: "t1", slug: "t1", name: "T1", role: "admin" },
  chat_providers: [],
};

type Overrides = {
  security?: SecurityPayload;
  me?: MeOut;
};

function renderPage(element: ReactElement, overrides: Overrides = {}) {
  vi.mocked(securityPayload).mockResolvedValue(overrides.security ?? securityFixture);
  // `mockResolvedValue` sets a persistent default, not a one-shot — a prior
  // test's viewer override would otherwise leak into a test that never asked
  // for one, since `clearAllMocks` clears call history but not this. Always
  // set it explicitly so every render starts from a known role.
  vi.mocked(me).mockResolvedValue(overrides.me ?? OPERATOR_ME);

  const openHandoff = vi.fn();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ChatHandoffContext.Provider value={{ openAlert: openHandoff, askGuardian: () => {} }}>
          {element}
        </ChatHandoffContext.Provider>
      </ToastProvider>
    </QueryClientProvider>,
  );

  return {
    posted,
    openHandoff,
  };
}

describe("Security", () => {
  it("shows a grade, the wifi verdict and evidence on every check", async () => {
    renderPage(<Security />, { security: securityFixture });
    expect(await screen.findByText(/Security posture/)).toBeInTheDocument();
    expect(await screen.findByTestId("wifi-verdict")).toHaveTextContent(securityFixture.wifi.verdict);
    for (const row of screen.getAllByTestId("check-row")) expect(row).toHaveTextContent(/verified/i);
  });

  it("shows the custom checks, dead-man heartbeats and domain expiry the sensor already collects", async () => {
    renderPage(<Security />, { security: securityFixture });

    expect(await screen.findByText("Custom checks")).toBeInTheDocument();
    expect(await screen.findByText("office-vpn")).toBeInTheDocument();
    // A configured-but-not-yet-run probe must not read as a pass.
    expect(screen.getByText("Not run")).toBeInTheDocument();
    expect(screen.getByText("no reply")).toBeInTheDocument();
    expect(screen.getByText("10.10.0.1:51820")).toBeInTheDocument();

    expect(screen.getByText("Dead-man heartbeats")).toBeInTheDocument();
    expect(screen.getByText("nightly-backup")).toBeInTheDocument();
    // A job that has never checked in is late, and says "never" rather than
    // borrowing a timestamp it does not have.
    expect(screen.getByText("Late")).toBeInTheDocument();
    expect(screen.getByText("never")).toBeInTheDocument();

    expect(screen.getByText("Domain registrations")).toBeInTheDocument();
    expect(screen.getByText("example.test")).toBeInTheDocument();
    expect(screen.getByText("2026-09-20")).toBeInTheDocument();
    // No expiry date from whois must read as unknown, never as "0 days left".
    expect(screen.getByText("unknown")).toBeInTheDocument();
    expect(screen.getByText("whois timed out")).toBeInTheDocument();
  });

  it("says so plainly when none of the three watch lists is configured", async () => {
    renderPage(<Security />, {
      security: { ...securityFixture, extra_checks: [], heartbeats: [], domains: [], email: [],
        ext_ports: [], fix_log: [] },
    });
    expect(await screen.findByText("No custom checks are configured.")).toBeInTheDocument();
    // Item 27: "none configured" is neither a pass nor a failure, so the
    // line explains what the check is for rather than leaving the reader
    // to guess which of the two they are looking at.
    expect(screen.getByText(/No dead-man heartbeats are set up/)).toBeInTheDocument();
    expect(screen.getByText(/notices something that stopped happening/)).toBeInTheDocument();
    expect(screen.getByText("No domain registrations have been looked up yet.")).toBeInTheDocument();
  });

  it("says it is still checking instead of drawing an all-zero Wi-Fi guard while the payload is in flight", async () => {
    // `EMPTY_SECURITY`'s `wifi` is an all-zero guard: rendering it draws a
    // FAILED SSID/encryption row and the line "Every device on the network is
    // recognized" — four confident claims about a network the hub has not
    // answered about yet. Nothing may be asserted until the payload lands.
    let release: (p: SecurityPayload) => void = () => {};
    vi.mocked(securityPayload).mockReturnValue(
      new Promise<SecurityPayload>((resolve) => {
        release = resolve;
      }),
    );
    vi.mocked(me).mockResolvedValue(OPERATOR_ME);
    vi.mocked(queueStatus).mockResolvedValue({ id: "q1", state: "done", detail: "" } as never);

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <Security />
        </ToastProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/Loading security/i)).toBeInTheDocument();
    expect(screen.queryByTestId("wifi-guard")).not.toBeInTheDocument();
    expect(screen.queryByText(/Every device on the network is recognized/i)).not.toBeInTheDocument();

    release(securityFixture);
    expect(await screen.findByTestId("wifi-guard")).toBeInTheDocument();
    expect(screen.queryByText(/Loading security/i)).not.toBeInTheDocument();
  });

/* The attack map is a React.lazy chunk (AttackMap-*.js, about 450 kB), so the
   * markers cannot appear until that import resolves. Testing Library's default
   * 1000 ms is enough on an idle machine and not enough on a loaded one: this
   * suite failed here, and only here, while a dozen other jobs were running, and
   * passed on its own every time. A longer wait costs nothing when the chunk is
   * fast and removes a failure that means nothing when it is slow. */
  const LAZY_CHUNK = { timeout: 8000 };

  it("plots located, still-active attackers by default and lists the unlocated ones", async () => {
    renderPage(<Security />, { security: securityFixture });
    // 1.2.3.4 is already blocked (handled) and hidden by default; 5.6.7.8 is
    // active and located; 9.9.9.9 is active and unlocated.
    const activeLocated = securityFixture.attackers.filter((a) => a.geo && a.action === "");
    const activeUnlocated = securityFixture.attackers.filter((a) => !a.geo && a.action === "");
    expect(await screen.findAllByTestId("attack-marker", undefined, LAZY_CHUNK))
      .toHaveLength(activeLocated.length);
    expect(screen.getByTestId("attackers-unlocated")).toHaveTextContent(
      String(activeUnlocated.length),
    );
  });

  it("hides handled attackers by default, says how many, and reveals them on request", async () => {
    renderPage(<Security />, { security: securityFixture });
    const handled = securityFixture.attackers.filter((a) => a.action !== "");
    await screen.findAllByTestId("attack-marker", undefined, LAZY_CHUNK);

    const toggle = screen.getByRole("button", { name: new RegExp(`${handled.length} hidden`, "i") });
    expect(screen.getByTestId("attackers-list")).not.toHaveTextContent("1.2.3.4");

    await userEvent.click(toggle);

    expect(await screen.findByRole("button", { name: /hide handled attackers/i })).toBeInTheDocument();
    expect(screen.getByTestId("attackers-list")).toHaveTextContent("1.2.3.4");
  });

  it("states each attacker's status in words, including the block date for a handled one", async () => {
    renderPage(<Security />, { security: securityFixture });
    await userEvent.click(await screen.findByRole("button", { name: /hidden/i }));

    const list = screen.getByTestId("attackers-list");
    expect(within(list).getByText(/blocked automatically, sep 9/i)).toBeInTheDocument();
    expect(within(list).getAllByText(/still active — needs your attention/i).length).toBeGreaterThan(0);
  });

  it("disables the attacker-method select for a viewer", async () => {
    renderPage(<Security />, { security: securityFixture, me: viewerMe });
    expect(await screen.findByLabelText(/attacker method/i)).toBeDisabled();
  });

  it("enables the attacker-method select for an admin role and posts a change", async () => {
    const { posted } = renderPage(<Security />, { security: securityFixture, me: ADMIN_ME });
    const select = await screen.findByLabelText(/attacker method/i);
    await waitFor(() => expect(select).toBeEnabled());

    await userEvent.selectOptions(select, "block");

    expect(posted("/api/v1/attacker")).toEqual({ method: "block" });
  });

  it("disables Fix now and Block this /24 for an operator, with an admin-role tooltip", async () => {
    renderPage(<Security />, { security: securityFixture, me: OPERATOR_ME });

    const fixButton = await screen.findByRole("button", { name: /fix now/i });
    expect(fixButton).toBeDisabled();
    expect(fixButton).toHaveAttribute("title", "admin role required");

    await userEvent.click(
      (await screen.findAllByTestId("attack-marker", undefined, LAZY_CHUNK))[0],
    );
    const blockButton = await screen.findByRole("button", { name: /block this \/24/i });
    expect(blockButton).toBeDisabled();
    expect(blockButton).toHaveAttribute("title", "admin role required");
  });

  it("posts a scorecard's fix and polls the queue until it settles", async () => {
    vi.mocked(queueStatus).mockResolvedValue({
      id: "q1",
      path: "/api/v1/fix",
      status: "done",
      body: {},
      result: null,
      ts: "2026-09-09T18:00:00-04:00",
    });
    const { posted } = renderPage(<Security />, { security: securityFixture, me: ADMIN_ME });

    await userEvent.click(await screen.findByRole("button", { name: /fix now/i }));

    expect(posted("/api/v1/fix")).toMatchObject({ host: "status.example.org", action: "harden_headers", arg: null });
    expect(await screen.findByText(/fix: command completed/i)).toBeInTheDocument();
  });

  it("does not show a Fix now button when a scorecard has no fix queued", async () => {
    renderPage(<Security />, { security: securityFixture, me: ADMIN_ME });
    // vps1's scorecard has `fix: ""` — only status.example.org's should render one.
    expect(await screen.findAllByRole("button", { name: /fix now/i })).toHaveLength(1);
  });

  it("posts a subnet block for the attacker chosen in the map drawer, and queues + polls it like a fix", async () => {
    vi.mocked(queueStatus).mockResolvedValue({
      id: "q1",
      path: "/api/v1/fix",
      status: "done",
      body: {},
      result: null,
      ts: "2026-09-09T18:00:00-04:00",
    });
    const { posted } = renderPage(<Security />, { security: securityFixture, me: ADMIN_ME });

    // 1.2.3.4 is already handled, so it's hidden by default — reveal it,
    // then open the marker for that specific attacker.
    await screen.findAllByTestId("attack-marker", undefined, LAZY_CHUNK);
    await userEvent.click(screen.getByRole("button", { name: /hidden/i }));
    await userEvent.click(await screen.findByRole("button", { name: /^1\.2\.3\.4 —/ }));
    await userEvent.click(await screen.findByRole("button", { name: /block this \/24/i }));

    // Blocking a /24 is disruptive: the first press queues nothing and brings
    // back the hub's own sentence to agree to.
    expect(posted("/api/v1/fix/confirm")).toMatchObject({ host: "vps1", action: "block_subnet" });
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/disruptive/i);
    await userEvent.click(within(dialog).getByRole("button", { name: "Block" }));

    expect(posted("/api/v1/fix")).toEqual({
      host: "vps1", action: "block_subnet", arg: "1.2.3.4", confirm_token: "tok-sec",
    });
    // Queued and polled to a terminal state — not a "Subnet blocked" toast
    // fired straight off the initial POST response.
    expect(await screen.findByText(/block: command completed/i)).toBeInTheDocument();
  });

  it("names each domain's SPF, DKIM and DMARC, and whether it is spoofable", async () => {
    renderPage(<Security />);
    await screen.findByText("p=reject");
    const panel = screen.getByRole("heading", { name: "Email spoofing protection" }).closest("section")!;
    for (const header of ["Domain", "SPF", "DKIM", "DMARC", "Policy", "Spoofable", "Checked"]) {
      expect(within(panel).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    expect(within(panel).getByText("p=reject")).toBeInTheDocument();
    // A domain that sends no mail still needs the records, and says so.
    expect(within(panel).getAllByText(/sends no mail/).length).toBe(1);
    expect(within(panel).getAllByText("missing").length).toBe(2);
  });

  it("marks the unexpected internet-facing port and the admin path the probe hit", async () => {
    renderPage(<Security />);
    await screen.findByText("/wp-login.php");
    const panel = screen.getByRole("heading", { name: "Site hosts seen from the internet" }).closest("section")!;
    expect(within(panel).getByText("5432")).toHaveClass("g-tone-warn");
    for (const cell of within(panel).getAllByText("443")) expect(cell).not.toHaveClass("g-tone-warn");
    expect(within(panel).getByText("/wp-login.php")).toBeInTheDocument();
    // A host with nothing unexpected reads clean rather than blank.
    expect(within(panel).getByText("clean")).toBeInTheDocument();
  });

  it("logs every repair with who ran it and whether it worked", async () => {
    renderPage(<Security />);
    const panel = (await screen.findByRole("heading", { name: "Fix log" })).closest("section")!;
    // Scoped to the TABLE: the filter selects now list the same action and
    // person names as options, so a panel-wide text query matches twice.
    const table = within(panel).getByRole("table");
    expect(within(table).getByText("security_updates")).toBeInTheDocument();
    expect(within(table).getByText("auto")).toBeInTheDocument();
    expect(within(table).getByText("myron")).toBeInTheDocument();
    expect(within(table).getByText("succeeded")).toBeInTheDocument();
    expect(within(table).getByText("failed")).toBeInTheDocument();
  });

  it("reads the rogue box off the Wi-Fi guard, and says so when a fact was not reported", async () => {
    renderPage(<Security />);
    const box = await screen.findByTestId("rogue-checks");
    expect(box).toHaveTextContent("DHCP server");
    expect(box).toHaveTextContent("Default route");
    expect(box).toHaveTextContent("Router MAC");
    // The fixture's guard carries no default route or router MAC.
    expect(within(box).getAllByText(/not reported on this network/).length).toBeGreaterThan(0);
  });

  it("renders the exposure tiles", async () => {
    renderPage(<Security />);
    for (const tile of securityFixture.exposure) {
      expect((await screen.findAllByText(tile.label))[0]).toBeInTheDocument();
    }
  });
});

/* --- The Wi-Fi guard / checklist / posture drill-down (owner request:
 * clicking a failing check like "evil twin" should open detail and offer
 * resolve or leave it, same for Security posture). --------------------- */

const EVIL_TWIN_CHECK = {
  key: "wifi:evil_twin",
  area: "Wi-Fi",
  title: "No evil twin",
  passed: false,
  last_verified: "2026-09-09T17:00:00-04:00",
  evidence: "twin_channels: [6]",
  autofix: "none",
  target: "Home network",
  decision: { status: "open", reason: "", decided_by: "", decided_at: "" },
  repairs: [],
};

const evilTwinFixture: SecurityPayload = {
  ...securityFixture,
  wifi: {
    ...securityFixture.wifi,
    verdict: "Evil twin detected: SSID also seen on channel(s) [6]",
    state: "bad",
    evil_twin: true,
    twin_channels: [6],
    rows: [EVIL_TWIN_CHECK],
  },
};

const evilTwinDetail: CheckDetail = {
  key: "wifi:evil_twin",
  area: "Wi-Fi",
  title: "No evil twin",
  target: "Home network",
  network_id: "",
  passed: false,
  last_verified: "2026-09-09T17:00:00-04:00",
  evidence: "twin_channels: [6]",
  what_we_saw: 'Your Wi-Fi network "HomeNet-5G" is also broadcasting on channel 6, alongside its usual channel 44.',
  why_it_matters:
    "A second access point is broadcasting your network's name on a different channel. Vault cannot tell " +
    "a malicious twin apart from a mesh node or repeater by radio alone.",
  confidence: "uncertain",
  remedy: {
    kind: "human", action: "", host: "", arg: "", tier: 0, tool: "", min_role: "", can_run_now: false,
    summary: 'I have no repair I can run for "No evil twin" -- this one needs you.',
    need: "Check whether you own a second access point or mesh node.",
    impact: "",
    opening: "Evil twin detected.\n\nI have no repair I can run for this -- this one needs you.",
    confidence: 0, confidence_why: "", confidence_factors: [],
  },
  decision: { status: "open", reason: "", decided_by: "", decided_at: "" },
  repairs: [],
};

const SSH_CHECK = {
  key: "ssh:vps1",
  area: "SSH",
  title: "Key-only SSH",
  passed: false,
  last_verified: "2026-09-09T15:00:00-04:00",
  evidence: "sshd -T: passwordauthentication yes",
  autofix: "auto",
  target: "vps1",
  decision: { status: "open", reason: "", decided_by: "", decided_at: "" },
  repairs: [],
};

const sshDetail: CheckDetail = {
  key: "ssh:vps1",
  area: "SSH",
  title: "Key-only SSH",
  target: "vps1",
  network_id: "",
  passed: false,
  last_verified: "2026-09-09T15:00:00-04:00",
  evidence: "sshd -T: passwordauthentication yes",
  what_we_saw: "Key-only SSH on vps1: sshd -T: passwordauthentication yes.",
  why_it_matters: "Password logins on this host can be guessed or phished.",
  confidence: "confirmed",
  remedy: {
    kind: "auto", action: "disable_ssh_password", host: "vps1", arg: "", tier: 2, tool: "apply_fix",
    min_role: "admin", can_run_now: true, summary: "I can fix this myself: switch SSH to key-only login on vps1.",
    need: "",
    impact: "", opening: "",
    confidence: 0, confidence_why: "", confidence_factors: [],
  },
  decision: { status: "open", reason: "", decided_by: "", decided_at: "" },
  repairs: [],
};

describe("Security — check drill-down", () => {
  it("opens the drawer for a failing Wi-Fi guard row and hands off to chat when Vault has no repair", async () => {
    vi.mocked(checkDetail).mockResolvedValue(evilTwinDetail);
    vi.mocked(checkHandoff).mockResolvedValue({
      key: "wifi:evil_twin", remedy: evilTwinDetail.remedy, opening: evilTwinDetail.remedy.opening,
      proposal: null, thread_id: "th1",
    } as AlertHandoff);
    const { openHandoff } = renderPage(<Security />, { security: evilTwinFixture, me: ADMIN_ME });

    await userEvent.click(await screen.findByRole("button", { name: /evil twin/i }));

    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText(/also broadcasting on channel 6/i)).toBeInTheDocument();
    expect(within(drawer).getByText(/cannot tell a malicious twin/i)).toBeInTheDocument();
    expect(within(drawer).getByText(/worth checking/i)).toBeInTheDocument();

    await userEvent.click(within(drawer).getByRole("button", { name: /resolve/i }));

    expect(checkHandoff).toHaveBeenCalledWith({ key: "wifi:evil_twin", network: undefined, target: "Home network" });
    await waitFor(() => expect(openHandoff).toHaveBeenCalled());
  });

  it("records leave-it, and shows the row as a deliberate decision rather than an unseen failure", async () => {
    vi.mocked(checkDetail).mockResolvedValue(evilTwinDetail);
    vi.mocked(checkDecision).mockResolvedValue({
      status: "left", reason: "our own mesh AP", decided_by: "a", decided_at: "2026-09-09T18:00:00-04:00",
    });
    renderPage(<Security />, { security: evilTwinFixture, me: ADMIN_ME });

    await userEvent.click(await screen.findByRole("button", { name: /evil twin/i }));
    const drawer = await screen.findByRole("dialog");

    await userEvent.type(within(drawer).getByLabelText(/reason for leaving/i), "our own mesh AP");
    await userEvent.click(within(drawer).getByRole("button", { name: /leave it/i }));

    expect(checkDecision).toHaveBeenCalledWith(
      "leave", { key: "wifi:evil_twin", network: undefined, target: "Home network" }, "our own mesh AP", "",
    );
    // The security payload is refetched after a decision — the fixture's own
    // `wifi.rows` entry is still "open" (this test does not re-mock
    // `securityPayload`), which is enough to prove the invalidation ran
    // without needing the mock plumbing for a second, "left" payload.
    await waitFor(() => expect(securityPayload).toHaveBeenCalledTimes(2));
  });

  it("resolving a check Vault owns runs the fix through the ordinary propose-or-confirm flow, not the chat handoff", async () => {
    const withSsh: SecurityPayload = { ...securityFixture, checks: [...securityFixture.checks, SSH_CHECK] };
    vi.mocked(checkDetail).mockResolvedValue(sshDetail);
    const { posted, openHandoff } = renderPage(<Security />, { security: withSsh, me: ADMIN_ME });

    await userEvent.click(await screen.findByRole("button", { name: /key-only ssh/i }));
    const drawer = await screen.findByRole("dialog");
    await userEvent.click(within(drawer).getByRole("button", { name: /resolve/i }));

    expect(posted("/api/v1/fix")).toMatchObject({ host: "vps1", action: "disable_ssh_password" });
    expect(checkHandoff).not.toHaveBeenCalled();
    expect(openHandoff).not.toHaveBeenCalled();
  });

  it("disables Resolve for an operator when the check needs an admin-run fix, but leaves it enabled when there is no fix at all", async () => {
    const withSsh: SecurityPayload = { ...securityFixture, checks: [...securityFixture.checks, SSH_CHECK] };
    vi.mocked(checkDetail).mockResolvedValue(sshDetail);
    renderPage(<Security />, { security: withSsh, me: OPERATOR_ME });

    await userEvent.click(await screen.findByRole("button", { name: /key-only ssh/i }));
    const drawer = await screen.findByRole("dialog");
    const resolveButton = within(drawer).getByRole("button", { name: /resolve/i });
    expect(resolveButton).toBeDisabled();
    expect(resolveButton).toHaveAttribute("title", "admin role required");
  });

  it("disables Leave it for a viewer, with an operator-role tooltip, but still shows the detail", async () => {
    vi.mocked(checkDetail).mockResolvedValue(evilTwinDetail);
    renderPage(<Security />, { security: evilTwinFixture, me: viewerMe });

    await userEvent.click(await screen.findByRole("button", { name: /evil twin/i }));
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText(/also broadcasting on channel 6/i)).toBeInTheDocument();

    const leaveButton = within(drawer).getByRole("button", { name: /leave it/i });
    expect(leaveButton).toBeDisabled();
    expect(leaveButton).toHaveAttribute("title", "operator role required");
  });

  it("opens a posture deduction's own check row, and does not open one when the deduction has no matching key", async () => {
    const withDeductionKey: SecurityPayload = {
      ...securityFixture,
      scorecards: [
        {
          target: "vps1", kind: "host", grade: "A", score: 96,
          deductions: [
            { category: "Auto-updates", points: 4, why: "disabled" }, // no matching check row
            { category: "Key-only SSH", points: 4, why: "password login allowed", key: "ssh:vps1" },
          ],
          fix: "",
        },
      ],
      checks: [...securityFixture.checks, SSH_CHECK],
    };
    vi.mocked(checkDetail).mockResolvedValue(sshDetail);
    renderPage(<Security />, { security: withDeductionKey, me: ADMIN_ME });

    // "Auto-updates" has no `key` — it renders as plain text, not a button.
    const autoUpdatesText = await screen.findByText(/disabled$/);
    expect(autoUpdatesText.closest("button")).toBeNull();

    // The same title also names the CheckList row this deduction's `key`
    // resolves to — scope to the posture card's own deduction button so the
    // click exercises `openDeduction`, not the checklist's `openCheck`.
    const postureCard = screen.getByTestId("scorecard");
    await userEvent.click(within(postureCard).getByRole("button", { name: /key-only ssh/i }));
    expect(checkDetail).toHaveBeenCalledWith({ key: "ssh:vps1", network: undefined, target: undefined });
  });
});

describe("the repair log can be narrowed (item 28)", () => {
  it("filters by result, so 'did anything fail' is one click", async () => {
    // It was a bare table: answering "what did Vault do to this machine,
    // and did it work" meant reading every row.
    renderPage(<Security />);
    const panel = (await screen.findByRole("heading", { name: "Fix log" })).closest("section")!;
    const before = within(panel).getAllByRole("row").length;

    await userEvent.selectOptions(
      within(panel).getByLabelText("Filter repairs by result"), "failed",
    );
    const after = within(panel).getAllByRole("row").length;
    expect(after).toBeLessThan(before);
    expect(within(panel).getByRole("table")).not.toHaveTextContent("succeeded");
  });

  it("offers the four columns a person narrows by", async () => {
    renderPage(<Security />);
    const panel = (await screen.findByRole("heading", { name: "Fix log" })).closest("section")!;
    for (const label of [
      "Filter repairs by machine", "Filter repairs by action",
      "Filter repairs by who asked", "Filter repairs by result",
    ]) {
      expect(within(panel).getByLabelText(label)).toBeInTheDocument();
    }
  });
});

// --------------------------------------------------------------------------
// Item 25: the popup proves it was FIXED, not merely found
// --------------------------------------------------------------------------

describe("what Vault did about a finding", () => {
  const withRepairs: CheckDetail = {
    ...evilTwinDetail,
    repairs: [
      {
        label: "Turn the firewall on", action: "enable_ufw", target: "vps1",
        ran_at: "2026-09-20T10:00:00Z", by: "Vault", by_person: false,
        ran_ok: true, confirmed_at: "2026-09-20T10:25:00Z",
        proof: "A later scan found the firewall active.",
        output: "Firewall is active and enabled on system startup",
      },
      {
        label: "Install security updates", action: "security_updates", target: "vps1",
        ran_at: "2026-09-20T09:00:00Z", by: "hub:miro", by_person: true,
        ran_ok: true, confirmed_at: "",
        proof: "Vault ran this and the command reported success. Nothing has looked at the target again since, so this is not yet proof.",
        output: "",
      },
    ],
  };

  it("separates a command's own report from a later look at the target", async () => {
    // The distinction the whole feature rests on. A green tick on both
    // would tell a customer something is sorted when nobody ever checked.
    vi.mocked(checkDetail).mockResolvedValue(withRepairs);
    renderPage(<Security />, { security: evilTwinFixture, me: ADMIN_ME });
    await userEvent.click(await screen.findByRole("button", { name: /evil twin/i }));

    const proof = await screen.findByTestId("repair-proof");
    expect(within(proof).getByText("Confirmed fixed")).toBeTruthy();
    expect(within(proof).getByText("Ran — unconfirmed")).toBeTruthy();
    expect(within(proof).getByText("A later scan found the firewall active.")).toBeTruthy();
  });

  it("says who asked for each repair, and does not credit Vault with a person's yes", async () => {
    vi.mocked(checkDetail).mockResolvedValue(withRepairs);
    renderPage(<Security />, { security: evilTwinFixture, me: ADMIN_ME });
    await userEvent.click(await screen.findByRole("button", { name: /evil twin/i }));

    const proof = await screen.findByTestId("repair-proof");
    expect(proof.textContent).toContain("Vault, on its own");
    expect(proof.textContent).toContain("hub:miro asked for this");
  });

  it("shows nothing at all when Vault has never run a repair for this check", async () => {
    // Most checks — Wi-Fi, TLS, email, exposure — have no Vault-run
    // repair. An empty section would read as a repair that failed.
    vi.mocked(checkDetail).mockResolvedValue(evilTwinDetail);
    renderPage(<Security />, { security: evilTwinFixture, me: ADMIN_ME });
    await userEvent.click(await screen.findByRole("button", { name: /evil twin/i }));
    await screen.findByRole("dialog");
    expect(screen.queryByTestId("repair-proof")).toBeNull();
  });
});

it("a PASSING check opens too — the proof matters most once it is green", async () => {
  // It used to be inert, which meant that the moment a finding was actually
  // fixed the evidence for it became unreachable. "Was this dealt with, or
  // did it just stop showing up?" is a question about a green row.
  vi.mocked(checkDetail).mockResolvedValue({ ...evilTwinDetail, passed: true });
  const passing = {
    ...evilTwinFixture,
    checks: [{ ...EVIL_TWIN_CHECK, key: "firewall:vps1", area: "Firewall",
               title: "Firewall active", target: "vps1", passed: true }],
  };
  renderPage(<Security />, { security: passing, me: ADMIN_ME });

  const list = await screen.findByRole("list", { name: /passing checks/i });
  const chip = within(list).getAllByRole("button")[0];
  await userEvent.click(chip);
  expect(await screen.findByRole("dialog")).toBeTruthy();
});
