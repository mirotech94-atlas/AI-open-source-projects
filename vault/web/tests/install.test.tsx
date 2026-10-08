// The regression guard for Task 61.
//
// Vault used to advertise `curl -fsSL https://get.guardian.sh/mac | sh` and
// `docker run … ghcr.io/guardian/sensor`. Neither host belongs to Vault's
// operator: get.guardian.sh resolves to a third party, so following the
// dashboard's own instructions piped a stranger's script into a root shell,
// and the ghcr image was never published (401 on pull). The fix is that every
// advertised command names exactly one host — the origin the dashboard itself
// is served from — because that is the only host we can prove is the
// customer's own hub.
//
// So this file is written as a *sweep*, not as an assertion about one string:
// it renders every surface that prints an install command, in every platform
// state that surface offers, pulls every URL and container-image reference out
// of the rendered text, and fails on anything that is not this app's origin.
// A future edit that hard-codes `my.guarder.io`, resurrects a `ghcr.io` path,
// or invents `get.guardian.sh` again fails here regardless of which page or
// which platform tab it is hidden behind.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import { installCommand, PLATFORMS } from "../src/lib/install";
import type { EnrolCodeOut, MeOut } from "../src/api";
import Onboarding from "../src/pages/Onboarding";
import { Networks } from "../src/pages/Settings/Networks";
import Sensors from "../src/pages/Settings/Sensors";
import type { Network, NetworksPayload, SettingsPayload } from "../src/types";

vi.mock("../src/lib/live", () => ({
  useLiveFeed: () => ({ status: "open" as const, last: null, on: () => () => {}, dropped: 0 }),
}));

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return { ...actual, me: vi.fn(), generateEnrolCode: vi.fn() };
});

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    saveSettingsSection: vi.fn().mockResolvedValue({ ok: true }),
    devicesPayload: vi.fn().mockResolvedValue({ devices: [], networks: [] }),
    updateDevice: vi.fn().mockResolvedValue({ ok: true }),
    networksPayload: vi.fn(),
    createNetwork: vi.fn().mockResolvedValue({ ok: true }),
    revokeSensor: vi.fn().mockResolvedValue({ ok: true }),
  };
});

import { generateEnrolCode, me } from "../src/api";
import { networksPayload } from "../src/lib/api";

const CODE = "ABCD2345";
const ORIGIN = window.location.origin; // jsdom: http://localhost:3000

const enrolFixture: EnrolCodeOut = {
  code: CODE,
  expires: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
  sensor_id: "s1",
};

const tenant = { id: "t1", slug: "home", name: "Home", role: "admin" as const };

const meFixture: MeOut = {
  // Added by the chat panel's provider caption; every MeOut fixture carries it.
  chat_providers: [],
  user: { username: "miro", display_name: "Miro", is_superadmin: false },
  tenants: [tenant],
  active_tenant: tenant,
};

const network: Network = {
  id: "net_home",
  slug: "home",
  name: "Home network",
  kind: "home",
  cameras: 0,
  subnet_prefix: "192.168.1",
  expected_gateway: "192.168.1.1",
  ssid: "",
  sensor_id: null,
  sensor_name: "",
  colour: "",
  is_primary: true,
  status: "no-sensor",
  devices: 3,
  online: 2,
  unverified: 1,
  created: "2026-09-01T00:00:00Z",
  traffic_mode: "none",
};

const networksFixture: NetworksPayload = { networks: [network] };

// Only the fields these three panels read; the full SettingsPayload is 20+
// sections and none of the rest is on the path to an install command.
const settingsFixture = { sensors: [], networks: [network] } as unknown as SettingsPayload;

function wrap(node: ReactElement): ReactElement {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter>{node}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(me).mockResolvedValue(meFixture);
  vi.mocked(generateEnrolCode).mockResolvedValue(enrolFixture);
  vi.mocked(networksPayload).mockResolvedValue(networksFixture);
  window.localStorage.clear();
});

// --- the sweep --------------------------------------------------------------

/** Every `http(s)://…` in a command, plus anything that looks like a
 *  container image reference in a `docker …` command. Deliberately greedy:
 *  the point is to notice a host, not to parse shell. */
function hostsIn(command: string): string[] {
  const urls = command.match(/https?:\/\/[^\s'"|)]+/g) ?? [];
  const hosts = urls.map((u) => new URL(u).origin);
  // A `docker run … registry/path/image` reference carries a host with no
  // scheme, so URL parsing would miss it entirely — which is exactly how
  // `ghcr.io/guardian/sensor` survived review the first time.
  for (const token of command.split(/\s+/)) {
    if (/^[a-z0-9-]+(\.[a-z0-9-]+)+\/\S+$/.test(token)) hosts.push(token);
  }
  return hosts;
}

function assertOwnOriginOnly(command: string): void {
  const hosts = hostsIn(command);
  expect(hosts.length, `no host at all in: ${command}`).toBeGreaterThan(0);
  for (const host of hosts) {
    expect(host, `advertised command names a host that is not this app: ${command}`).toBe(ORIGIN);
  }
}

describe("advertised install commands name no host but our own", () => {
  it("the shared helper does, for any origin", () => {
    for (const origin of ["https://my.guarder.io", "https://hub.example.org:8443", ORIGIN]) {
      const command = installCommand(CODE, origin);
      expect(command).toContain(`${origin}/install`);
      expect(command).toContain(`--code ${CODE}`);
      for (const host of hostsIn(command)) expect(host).toBe(new URL(origin).origin);
    }
  });

  it("offers only platforms that actually have a distribution channel", () => {
    // Docker was dropped, not repointed: no Vault sensor image is
    // published anywhere, and inventing a registry path is the same defect as
    // the fabricated domain. Re-add it here the day an image exists.
    expect(PLATFORMS.map((p) => p.value)).toEqual(["macos", "linux"]);
  });

  it("Onboarding's install step, on every platform it offers", async () => {
    render(wrap(<Onboarding />));

    await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home");
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
    await screen.findByText(/which networks should Vault watch/i);
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));

    for (const platform of PLATFORMS) {
      await userEvent.click(await screen.findByRole("button", { name: platform.label }));
      const shown = await screen.findByText(/curl -fsSL/);
      assertOwnOriginOnly(shown.textContent ?? "");
    }
  });

  it("Settings → Networks' enrol panel, on every platform it offers", async () => {
    render(wrap(<Networks settings={settingsFixture} role="admin" />));

    await userEvent.click(await screen.findByRole("button", { name: /install a sensor|enrol/i }));
    for (const platform of PLATFORMS) {
      await userEvent.click(await screen.findByRole("button", { name: platform.label }));
      const shown = await screen.findByText(/curl -fsSL/);
      assertOwnOriginOnly(shown.textContent ?? "");
    }
  });

  it("Settings → Sensors' enrol panel", async () => {
    render(wrap(<Sensors settings={settingsFixture} role="admin" onInvalidate={() => {}} tenantId="t1" />));

    await userEvent.click(await screen.findByRole("button", { name: /generate enrol code/i }));
    const shown = await screen.findByText(/curl -fsSL/);
    assertOwnOriginOnly(shown.textContent ?? "");
  });

  it("hostsIn would have caught both of the historical fabrications", () => {
    // Guard the guard: if `hostsIn` ever stops seeing these, the sweep above
    // is silently vacuous.
    expect(hostsIn("curl -fsSL https://get.guardian.sh/mac | sh")).toContain("https://get.guardian.sh");
    expect(hostsIn("docker run -d ghcr.io/guardian/sensor enrol X")).toContain("ghcr.io/guardian/sensor");
  });
});
