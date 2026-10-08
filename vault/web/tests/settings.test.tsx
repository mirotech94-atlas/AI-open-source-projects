import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import Settings from "../src/pages/Settings";
import type { EnrolCodeOut, MeOut } from "../src/api";
import type { AuditPayload, HostsPayload, SettingsPayload, SitesPayload } from "../src/types";

// --- captured writes ---------------------------------------------------------

const { postedCalls } = vi.hoisted(() => {
  return { postedCalls: [] as { path: string; body: unknown }[] };
});

function posted(path: string): unknown {
  const call = [...postedCalls].reverse().find((c) => c.path === path);
  return call?.body;
}

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    settingsPayload: vi.fn(),
    hostsPayload: vi.fn(),
    sitesPayload: vi.fn(),
    auditPayload: vi.fn(),
    billingPayload: vi.fn(),
    saveSettingsSection: vi.fn((section: string, body: unknown) => {
      postedCalls.push({ path: `/api/v1/settings/${section}`, body });
      return Promise.resolve({});
    }),
    removeUser: vi.fn((username: string) => {
      postedCalls.push({ path: "/api/v1/settings/users", body: { username } });
      return Promise.resolve({});
    }),
    revokeSensor: vi.fn((id: string) => {
      postedCalls.push({ path: `/api/v1/settings/sensors/${id}/revoke`, body: {} });
      return Promise.resolve({});
    }),
    // "Re-run setup" clears THIS user's wizard state on the hub — there is no
    // browser flag behind it any more.
    saveOnboarding: vi.fn((body: unknown) => {
      postedCalls.push({ path: "/api/v1/onboarding", body });
      return Promise.resolve({
        path: "setup", step: "", completed: false, skipped: false, show: true, tenant_ready: false,
      });
    }),
    createTenant: vi.fn((body: unknown) => {
      postedCalls.push({ path: "/api/v1/settings/tenants", body });
      return Promise.resolve({});
    }),
    updateTenant: vi.fn((id: string, body: unknown) => {
      postedCalls.push({ path: `/api/v1/settings/tenants/${id}`, body });
      return Promise.resolve({});
    }),
    deleteTenant: vi.fn((id: string) => {
      postedCalls.push({ path: `/api/v1/settings/tenants/${id}`, body: { _deleted: true } });
      return Promise.resolve({});
    }),
  };
});

import {
  auditPayload,
  hostsPayload,
  saveSettingsSection,
  settingsPayload,
  sitesPayload,
} from "../src/lib/api";

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    me: vi.fn(),
    generateEnrolCode: vi.fn(),
  };
});

import { generateEnrolCode, me } from "../src/api";

afterEach(() => {
  postedCalls.length = 0;
  vi.clearAllMocks();
});

// --- fixtures ----------------------------------------------------------------

export const settingsFixture: SettingsPayload = {
  suggested_sites: [],
  profile: { owner: "Myron", email: "myron@example.com", address: "1 Home St", company: "Vault" },
  users: [
    { username: "myron", display_name: "Myron", email: "myron@example.com", role: "admin", is_superadmin: true, last_seen: null },
    { username: "ann", display_name: "Ann", email: "ann@example.com", role: "viewer", is_superadmin: false, last_seen: null },
  ],
  sites: [{ name: "status", url: "https://status.example.com", expect: [200], public: true, host: "vps1", ssh_host: "vps1" }],
  hosts: [{ name: "vps1", target: "root@vps1", optional: false, expect_services: ["nginx"], expect_docker: [] }],
  autofix: {
    enabled: true,
    max_per_day: 5,
    attacker_method: "auto",
    maintenance_window: { start: "02:00", end: "05:00" },
    cooldowns: { block_ip: 300 },
  },
  thresholds: {
    cert_days_warn: 30,
    disk_pct_warn: 80,
    mem_pct_warn: 85,
    load_per_core_warn: 1.5,
    http_latency_warn_s: 2,
  },
  appearance: { theme: "system", accent: "blue" },
  sensors: [
    { id: "s1", name: "home-sensor", platform: "linux", version: "1.2.0", status: "online", last_seen: "2026-09-10T12:00:00-04:00", age_s: 30 },
  ],
  tenants: [
    { id: "t1", slug: "t1", name: "Home", brand: {}, ntfy_topic: "guardian-home", users: 2, sensors: 1, demo: false },
  ],
};

export const hostsFixture: HostsPayload = {
  hosts: [
    {
      name: "prod",
      address: "10.0.0.5",
      reachable: true,
      hostname: "prod",
      uptime: "10d",
      load1: 0.4,
      load_per_core: 0.3,
      disk_pct: 56,
      mem_pct: 40,
      failed_ssh_24h: 0,
      banned: 0,
      fail2ban: true,
      reboot_required: false,
      pending_updates: 0,
      security_updates: 0,
      cve_fixable: 0,
      hardening: { score: 90, grade: "A", checks: [] },
      ports: [],
      docker: [],
      services: [],
      gauges: [],
      stale: false,
      stale_note: "",
      last_checked: "",
    },
  ],
};

export const sitesFixture: SitesPayload = {
  sites: [
    {
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
      latency_spark: [],
      cert_days: 60,
      cert_expires: "2026-11-01",
      cert_issuer: "Let's Encrypt",
      dns_a: ["1.2.3.4"],
      headers: { hsts: true, nosniff: true, frame: true, csp: true, https_redirect: true, old_tls: false, server: "" },
      incidents_7d: 0,
      public: true,
      stale: false,
      stale_note: "",
      last_checked: "",
    },
  ],
  charts: [],
};

const auditFixture: AuditPayload = {
  rows: [
    { ts: "2026-09-10T10:00:00-04:00", user: "myron", role: "admin", action: "thresholds.update", target: "t1", ip: "1.2.3.4", body: { disk_pct_warn: 85 } },
  ],
};

const enrolFixture: EnrolCodeOut = { code: "ABCD2345", expires: new Date(Date.now() + 15 * 60 * 1000).toISOString(), sensor_id: "s1" };

export const viewerMe: MeOut = {
  user: { username: "v", display_name: "Viewer", is_superadmin: false },
  tenants: [{ id: "t1", slug: "t1", name: "T1", role: "viewer" }],
  active_tenant: { id: "t1", slug: "t1", name: "T1", role: "viewer" },
  chat_providers: [],
};

const ADMIN_ME: MeOut = {
  user: { username: "myron", display_name: "Myron", is_superadmin: false },
  tenants: [{ id: "t1", slug: "t1", name: "T1", role: "admin" }],
  active_tenant: { id: "t1", slug: "t1", name: "T1", role: "admin" },
  chat_providers: [],
};

const SUPERADMIN_ME: MeOut = {
  user: { username: "myron", display_name: "Myron", is_superadmin: true },
  tenants: [{ id: "t1", slug: "t1", name: "T1", role: "admin" }],
  active_tenant: { id: "t1", slug: "t1", name: "T1", role: "admin" },
  chat_providers: [],
};

// --- helpers -------------------------------------------------------------

type Overrides = {
  settings?: SettingsPayload;
  hosts?: HostsPayload;
  sites?: SitesPayload;
  audit?: AuditPayload;
  me?: MeOut;
  enrol?: EnrolCodeOut;
};

function renderPage(element: ReactElement, overrides: Overrides = {}, options: { route?: string } = {}) {
  vi.mocked(settingsPayload).mockResolvedValue(overrides.settings ?? settingsFixture);
  vi.mocked(hostsPayload).mockResolvedValue(overrides.hosts ?? hostsFixture);
  vi.mocked(sitesPayload).mockResolvedValue(overrides.sites ?? sitesFixture);
  vi.mocked(auditPayload).mockResolvedValue(overrides.audit ?? auditFixture);
  vi.mocked(me).mockResolvedValue(overrides.me ?? ADMIN_ME);
  vi.mocked(generateEnrolCode).mockResolvedValue(overrides.enrol ?? enrolFixture);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={[options.route ?? "/settings"]}>
          <Routes>
            <Route path="/settings" element={element} />
            <Route path="/settings/:section" element={element} />
            <Route path="/welcome" element={<div>Welcome placeholder</div>} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );

  return { posted };
}

describe("Settings", () => {
  it("saves thresholds and shows the live value beside each field", async () => {
    const { posted } = renderPage(<Settings />, {}, { route: "/settings/thresholds" });

    expect(await screen.findByText(/prod is at 56%/)).toBeInTheDocument();

    const input = screen.getByLabelText(/disk warn/i);
    await userEvent.clear(input);
    await userEvent.type(input, "85");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(posted("/api/v1/settings/thresholds")).toMatchObject({ disk_pct_warn: 85 });
  });

  // The form sends every field, not just the edited one, so an unset
  // threshold is written with whatever the input was seeded with. Seeding
  // from the input's `min` meant opening this page and pressing Save quietly
  // dropped every threshold the owner had never set to the lowest value the
  // control allows. Seed from the sensor's real default instead.
  it("saving a threshold the tenant never set writes its default, not the minimum", async () => {
    const bare = {
      ...settingsFixture,
      thresholds: { disk_pct_warn: 80 },   // every other threshold unset
    };
    const { posted } = renderPage(<Settings />, { settings: bare }, { route: "/settings/thresholds" });

    await screen.findByLabelText(/disk warn/i);
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(posted("/api/v1/settings/thresholds")).toMatchObject({
      cert_days_warn: 21,          // not 1, the range minimum
      mem_pct_warn: 90,            // not 50
      load_per_core_warn: 2,       // not 0.5
      http_latency_warn_s: 4,      // not 0.2 -- the value WEME's config ended up with
    });
  });

  it("shows no editable form when settings failed to load", async () => {
    // The 2026-09-22 bug: a failed load left `isLoading` false, the forms
    // mounted on the placeholder, and saving Auto-fix wrote its cap of 0.
    renderPage(<Settings />, {}, { route: "/settings/autofix" });
    vi.mocked(settingsPayload).mockReset();
    vi.mocked(settingsPayload).mockRejectedValue(new Error("hub busy"));
    // Re-render with the failing loader: a fresh page, same route.
    document.body.innerHTML = "";
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <MemoryRouter initialEntries={["/settings/autofix"]}>
            <Routes><Route path="/settings/:section" element={<Settings />} /></Routes>
          </MemoryRouter>
        </ToastProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/could not load settings/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/daily cap/i)).toBeNull();
    expect(screen.queryByRole("switch", { name: /automatic fixes/i })).toBeNull();
  });

  it("confirms before turning the auto-fix kill switch off", async () => {
    const { posted } = renderPage(<Settings />, {}, { route: "/settings/autofix" });

    await userEvent.click(await screen.findByRole("switch", { name: /automatic fixes/i }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent(/stop fixing things on its own/i);

    await userEvent.click(screen.getByRole("button", { name: /turn it off/i }));
    expect(posted("/api/v1/settings/autofix")).toMatchObject({ enabled: false });
  });

  it("turning the kill switch on does not need confirmation", async () => {
    const offSettings: SettingsPayload = {
      ...settingsFixture,
      autofix: { ...settingsFixture.autofix, enabled: false },
    };
    const { posted } = renderPage(<Settings />, { settings: offSettings }, { route: "/settings/autofix" });

    await userEvent.click(await screen.findByRole("switch", { name: /automatic fixes/i }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(posted("/api/v1/settings/autofix")).toMatchObject({ enabled: true });
  });

  it("shows an enrol code with the exact sensor command", async () => {
    renderPage(<Settings />, {}, { route: "/settings/sensors" });

    await userEvent.click(await screen.findByRole("button", { name: /generate enrol code/i }));

    expect(await screen.findByText(/curl -fsSL .*\/install .* --code ABCD2345/)).toBeInTheDocument();
  });

  it("re-runs setup from Sensors: clears this user's wizard state and opens the wizard", async () => {
    renderPage(<Settings />, {}, { route: "/settings/sensors" });

    await userEvent.click(await screen.findByRole("button", { name: /re-run setup/i }));

    expect(await screen.findByText("Welcome placeholder")).toBeInTheDocument();
    expect(posted("/api/v1/onboarding")).toMatchObject({
      completed: false, skipped: false, step: "",
    });
  });

  it("disables writes for a viewer and says which role is needed", async () => {
    renderPage(<Settings />, { me: viewerMe }, { route: "/settings/thresholds" });

    expect(await screen.findByLabelText(/disk warn/i)).toBeDisabled();
    expect(screen.getByText(/admin access/i)).toBeInTheDocument();
  });

  it("invites a member and surfaces the last-admin 422 inline, not as a toast", async () => {
    vi.mocked(saveSettingsSection).mockImplementationOnce((section, body) => {
      postedCalls.push({ path: `/api/v1/settings/${section}`, body });
      return Promise.resolve({});
    });
    renderPage(<Settings />, {}, { route: "/settings/users" });

    await userEvent.type(await screen.findByLabelText(/username/i), "newbie");
    await userEvent.type(screen.getByLabelText(/e-mail/i), "newbie@example.com");
    await userEvent.click(screen.getByRole("button", { name: /invite/i }));
    expect(posted("/api/v1/settings/users")).toMatchObject({ username: "newbie", email: "newbie@example.com" });

    // Removing the tenant's last admin 422s — the row's confirm dialog must
    // show the error, and the toast layer must stay empty.
    const { removeUser } = await import("../src/lib/api");
    vi.mocked(removeUser).mockRejectedValueOnce(new Error("cannot remove the tenant's last admin"));

    await userEvent.click(screen.getAllByRole("button", { name: /remove/i })[0]);
    const dialog = screen.getByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /remove/i }));

    expect(await screen.findByText(/last admin/i)).toBeInTheDocument();
    // The invite above already left one "good" toast; the 422 must not add
    // a second (alert-toned) one — it stays inline on the dialog instead.
    expect(document.querySelector(".g-toast.g-tone-alert")).toBeNull();
  });

  it("opens Sites & targets with a summary of what is actually monitored", async () => {
    renderPage(<Settings />, {}, { route: "/settings/targets" });

    const summary = await screen.findByTestId("monitored-summary");
    expect(summary).toHaveTextContent("1 watched, 1 on the public status page");
    expect(summary).toHaveTextContent("1 expected services and containers");
    expect(summary).toHaveTextContent("1 enrolled, 1 reporting");
    expect(summary).toHaveTextContent("up to 5/day");
  });

  it("says plainly when automatic fixes are off", async () => {
    renderPage(
      <Settings />,
      { settings: { ...settingsFixture, autofix: { ...settingsFixture.autofix, enabled: false } } },
      { route: "/settings/targets" },
    );
    expect(await screen.findByTestId("monitored-summary")).toHaveTextContent(
      "every repair waits for a person",
    );
  });

  it("blocks saving targets with an invalid site URL, inline", async () => {
    renderPage(<Settings />, {}, { route: "/settings/targets" });

    const urlInput = await screen.findByLabelText(/site url/i);
    await userEvent.clear(urlInput);
    await userEvent.type(urlInput, "ftp://bad");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(screen.getByText(/http/i)).toBeInTheDocument();
    expect(posted("/api/v1/settings/targets")).toBeUndefined();
  });

  it("shows a live report-cover preview on the Profile tab", async () => {
    renderPage(<Settings />, {}, { route: "/settings/profile" });
    expect(await screen.findByText(/Myron · Vault/)).toBeInTheDocument();
  });

  it("hides the Tenants tab for a non-superadmin and shows it for one", async () => {
    renderPage(<Settings />, { me: ADMIN_ME }, { route: "/settings" });
    expect(await screen.findByRole("tab", { name: /profile/i })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /tenants/i })).toBeNull();

    renderPage(<Settings />, { me: SUPERADMIN_ME }, { route: "/settings" });
    expect(await screen.findByRole("tab", { name: /tenants/i })).toBeInTheDocument();
  });

  it("gates the audit log behind admin read access rather than fetching for a viewer", async () => {
    renderPage(<Settings />, { me: viewerMe }, { route: "/settings/audit" });
    expect(await screen.findByText(/admin access/i)).toBeInTheDocument();
    expect(auditPayload).not.toHaveBeenCalled();
  });

  it("loads the audit log for an admin and expands a row's body", async () => {
    renderPage(<Settings />, {}, { route: "/settings/audit" });
    expect(await screen.findByText("thresholds.update")).toBeInTheDocument();

    await userEvent.click(screen.getByText("thresholds.update"));
    await waitFor(() => expect(screen.getByText(/disk_pct_warn/)).toBeInTheDocument());
  });

  // --- Fix round 1 ---------------------------------------------------------

  it("saves expect_services and expect_docker as arrays from comma-separated input", async () => {
    const { posted } = renderPage(<Settings />, {}, { route: "/settings/targets" });

    const servicesInput = await screen.findByLabelText(/expected services/i);
    await userEvent.clear(servicesInput);
    await userEvent.type(servicesInput, "nginx, postgresql");

    const containersInput = screen.getByLabelText(/expected containers/i);
    await userEvent.clear(containersInput);
    await userEvent.type(containersInput, "guardian-hub");

    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    const body = posted("/api/v1/settings/targets") as { hosts: { expect_services: string[]; expect_docker: string[] }[] };
    expect(body.hosts[0]).toMatchObject({
      expect_services: ["nginx", "postgresql"],
      expect_docker: ["guardian-hub"],
    });
  });

  it("exposes the site host field and saves it", async () => {
    const { posted } = renderPage(<Settings />, {}, { route: "/settings/targets" });

    const hostInput = await screen.findByLabelText(/site host/i);
    expect(hostInput).toHaveValue("vps1");
    await userEvent.clear(hostInput);
    await userEvent.type(hostInput, "vps2");

    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    const body = posted("/api/v1/settings/targets") as { sites: { host: string }[] };
    expect(body.sites[0]).toMatchObject({ host: "vps2" });
  });

  it("creates a tenant with an auto-derived, editable slug", async () => {
    const { posted } = renderPage(<Settings />, { me: SUPERADMIN_ME }, { route: "/settings/tenants" });

    const nameInput = await screen.findByLabelText(/new tenant name/i);
    await userEvent.type(nameInput, "Acme Corp!!");
    expect(screen.getByLabelText(/slug/i)).toHaveValue("acme-corp");

    await userEvent.click(screen.getByRole("button", { name: /create tenant/i }));

    expect(posted("/api/v1/settings/tenants")).toMatchObject({ name: "Acme Corp!!", slug: "acme-corp" });
  });

  it("edits and saves a tenant's brand name, accent, and domain", async () => {
    const { posted } = renderPage(<Settings />, { me: SUPERADMIN_ME }, { route: "/settings/tenants" });

    const nameInput = await screen.findByLabelText(/brand name for t1/i);
    await userEvent.type(nameInput, "Acme");
    await userEvent.type(screen.getByLabelText(/domain for t1/i), "reports.acme.example.com");
    await userEvent.selectOptions(screen.getByLabelText(/accent for t1/i), "#0c3fc9");

    await userEvent.click(screen.getByRole("button", { name: /save brand/i }));

    expect(posted("/api/v1/settings/tenants/t1")).toMatchObject({
      brand: { name: "Acme", accent: "#0c3fc9", domain: "reports.acme.example.com" },
    });
  });

  it("toggles a tenant's demo mode with a {demo: boolean} body", async () => {
    const { posted } = renderPage(<Settings />, { me: SUPERADMIN_ME }, { route: "/settings/tenants" });

    await userEvent.click(await screen.findByLabelText(/demo mode for t1/i));

    expect(posted("/api/v1/settings/tenants/t1")).toEqual({ demo: true });
  });

  it("saves the maintenance window as a {start, end} object", async () => {
    const { posted } = renderPage(<Settings />, {}, { route: "/settings/autofix" });

    const startInput = await screen.findByLabelText(/^start$/i);
    await userEvent.clear(startInput);
    await userEvent.type(startInput, "01:00");

    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(posted("/api/v1/settings/autofix")).toMatchObject({
      maintenance_window: { start: "01:00", end: "05:00" },
    });
  });

  it("moves focus into the confirm dialog, traps Tab, and restores focus on close", async () => {
    renderPage(<Settings />, {}, { route: "/settings/autofix" });

    const switchButton = await screen.findByRole("switch", { name: /automatic fixes/i });
    await userEvent.click(switchButton);

    const dialog = await screen.findByRole("alertdialog");
    const cancelBtn = within(dialog).getByRole("button", { name: /cancel/i });
    const confirmBtn = within(dialog).getByRole("button", { name: /turn it off/i });

    expect(document.activeElement).toBe(cancelBtn);

    await userEvent.tab();
    expect(document.activeElement).toBe(confirmBtn);

    // Tab from the last button wraps back to the first, never escaping the dialog.
    await userEvent.tab();
    expect(document.activeElement).toBe(cancelBtn);

    await userEvent.click(cancelBtn);
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(document.activeElement).toBe(switchButton);
  });

  it("validates the invite e-mail format inline before submitting", async () => {
    const { posted } = renderPage(<Settings />, {}, { route: "/settings/users" });

    await userEvent.type(await screen.findByLabelText(/username/i), "newbie");
    await userEvent.type(screen.getByLabelText(/e-mail/i), "not-an-email");
    await userEvent.click(screen.getByRole("button", { name: /invite/i }));

    expect(screen.getByText(/valid e-mail/i)).toBeInTheDocument();
    expect(posted("/api/v1/settings/users")).toBeUndefined();
  });

  it("offers Billing to a tenant admin, not only to the superadmin", async () => {
    // It used to be a superadmin-only stub. A tenant admin is who actually
    // buys, changes and cancels their own plan, so gating it to the product
    // owner made it a page only he could ever act on. Billing's own
    // behaviour lives in tests/billing.test.tsx.
    renderPage(<Settings />, { me: ADMIN_ME }, { route: "/settings" });
    const tabs = await screen.findByRole("tablist", { name: /settings section/i });
    expect(within(tabs).getByRole("tab", { name: "Billing" })).toBeInTheDocument();
    expect(within(tabs).queryByRole("tab", { name: "Tenants" })).not.toBeInTheDocument();
    expect(screen.queryByText(/superadmin-only/i)).not.toBeInTheDocument();
  });

  it("flags an out-of-range threshold inline instead of saving it", async () => {
    const { posted } = renderPage(<Settings />, {}, { route: "/settings/thresholds" });

    const input = await screen.findByLabelText(/disk warn/i);
    await userEvent.clear(input);
    await userEvent.type(input, "5");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(screen.getByText(/between 50% and 99%/i)).toBeInTheDocument();
    expect(posted("/api/v1/settings/thresholds")).toBeUndefined();
  });
});

// --------------------------------------------------------------------------
// Items 43/44: Vault offers what it has already found
// --------------------------------------------------------------------------

describe("suggested targets", () => {
  const withSuggestions = {
    ...settingsFixture,
    sites: [],
    suggested_sites: [
      { name: "nas", url: "https://192.168.1.50", why: "port 443 has been open here since 2026-09-03" },
    ],
  };

  it("offers a web server it can see, with the evidence for it", async () => {
    renderPage(<Settings />, { settings: withSuggestions }, { route: "/settings/targets" });
    const block = await screen.findByTestId("suggested-targets");
    expect(block.textContent).toContain("answer on a web port");
    expect(block.textContent).toContain("https://192.168.1.50");
    expect(block.textContent).toContain("port 443 has been open here");
  });

  it("Add moves it into the editable list and stops offering it", async () => {
    renderPage(<Settings />, { settings: withSuggestions }, { route: "/settings/targets" });
    const block = await screen.findByTestId("suggested-targets");
    await userEvent.click(within(block).getByRole("button", { name: "Add" }));
    // It is now an ordinary unsaved row the customer can rename…
    const urls = screen.getAllByLabelText("Site URL") as HTMLInputElement[];
    expect(urls.some((i) => i.value === "https://192.168.1.50")).toBe(true);
    // …and pressing Add twice cannot make two rows for one server.
    expect(screen.queryByTestId("suggested-targets")).toBeNull();
  });

  it("says nothing at all when there is nothing to offer", () => {
    renderPage(<Settings />, { settings: { ...settingsFixture, suggested_sites: [] } }, { route: "/settings/targets" });
    expect(screen.queryByTestId("suggested-targets")).toBeNull();
  });
});
