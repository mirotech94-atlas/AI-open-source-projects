import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnrolCodeOut, MeOut } from "../src/api";
import Onboarding from "../src/pages/Onboarding";
import type { DevicesPayload, NetworksPayload } from "../src/types";
import type { LiveEvent } from "../src/types";

// --- fake live feed: a controllable stand-in for the module singleton in
// `lib/live.ts`, the same "expose an `emit`" shape `shell.test.tsx`'s and
// `topology.test.tsx`'s own fakes use, extended here with real per-type
// listener bookkeeping since this suite needs to fire a `scan` frame and see
// the wizard react to it. ----------------------------------------------------

const listeners = new Map<string, Set<(event: LiveEvent) => void>>();

function emitLive(event: LiveEvent): void {
  const set = listeners.get(event.type);
  if (!set) return;
  for (const fn of Array.from(set)) fn(event);
}

const fakeLive = {
  status: "open" as const,
  last: null as LiveEvent | null,
  on: (type: string, fn: (event: LiveEvent) => void) => {
    let set = listeners.get(type);
    if (!set) {
      set = new Set();
      listeners.set(type, set);
    }
    set.add(fn);
    return () => set?.delete(fn);
  },
  dropped: 0,
};

vi.mock("../src/lib/live", () => ({
  useLiveFeed: () => fakeLive,
}));

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    me: vi.fn(),
    generateEnrolCode: vi.fn(),
  };
});

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    saveSettingsSection: vi.fn().mockResolvedValue({ ok: true }),
    devicesPayload: vi.fn(),
    updateDevice: vi.fn().mockResolvedValue({ ok: true }),
    // Step 2 reads the tenant's existing networks (to pre-fill the detected
    // subnet) and writes whatever the operator added.
    networksPayload: vi.fn(),
    createNetwork: vi.fn().mockResolvedValue({ ok: true }),
    // Which walkthrough the wizard draws is the hub's call now, per (user,
    // tenant) — and every "Next", "Skip for now" and "Done" is recorded there
    // rather than in `localStorage`.
    onboardingState: vi.fn(),
    saveOnboarding: vi.fn(),
  };
});

import { generateEnrolCode, me } from "../src/api";
import {
  createNetwork, devicesPayload, networksPayload, onboardingState, saveOnboarding,
  saveSettingsSection, updateDevice,
} from "../src/lib/api";
import type { OnboardingState } from "../src/types";
import { NO_TILES } from "./tiles";

/** A site where nothing has ever reported: the installing walkthrough. */
const SETUP_STATE: OnboardingState = {
  path: "setup", step: "", completed: false, skipped: false, show: true, tenant_ready: false, site_named: false, networks: [], next: { network_id: "", action: "declare_network" },
};

/** A newcomer on a site that already works: the orienting walkthrough. */
const TOUR_STATE: OnboardingState = {
  path: "tour", step: "", completed: false, skipped: false, show: true, tenant_ready: true, site_named: true, networks: [], next: null,
};

const freshMe: MeOut = {
  user: { username: "miro", display_name: "Miro", is_superadmin: false },
  tenants: [{ id: "t1", slug: "home", name: "Home", role: "admin" }],
  active_tenant: { id: "t1", slug: "home", name: "Home", role: "admin" },
  chat_providers: [],
};

const enrolFixture: EnrolCodeOut = {
  code: "ABCD2345",
  expires: new Date(Date.now() + 10 * 60_000).toISOString(),
  sensor_id: "s1",
};

const emptyDevices: DevicesPayload = { groups: [], chips: [], total: 0, online: 0, unverified: 0, cameras: 0, cameras_online: 0, tiles: NO_TILES, networks: [], charts: [] };

const devicesWithUnverified: DevicesPayload = {
  groups: [
    {
      group: "unknown",
      devices: [
        {
          mac: "aa:bb:cc:00:00:01",
          ip: "192.168.1.42",
          name: "",
          label: "",
          vendor: "Espressif",
          kind: "unknown",
          group: "unknown",
          trusted: false,
          always_on: false,
          quarantined: false,
          parent: null,
          risk: 10,
          presence: "home",
          online: true,
          first_seen: "2026-09-10T08:00:00Z",
          last_seen: "2026-09-10T08:05:00Z",
          ports: [],
          unverified: true,
          randomized: false,
          online_7d: null,
          camera_reasons: [],
          network: null,
        },
      ],
      network: null,
    },
  ],
  chips: [],
  total: 24,
  online: 20,
  unverified: 1,
  cameras: 0,
  cameras_online: 0,
  tiles: NO_TILES,
  networks: [],
  charts: [],
};

type Overrides = {
  networks?: NetworksPayload;
  me?: MeOut;
  enrol?: EnrolCodeOut;
  devices?: DevicesPayload;
  onboarding?: OnboardingState;
};

function renderPage(element: ReactElement, overrides: Overrides = {}) {
  vi.mocked(me).mockResolvedValue(overrides.me ?? freshMe);
  vi.mocked(generateEnrolCode).mockResolvedValue(overrides.enrol ?? enrolFixture);
  vi.mocked(devicesPayload).mockResolvedValue(overrides.devices ?? emptyDevices);
  vi.mocked(networksPayload).mockResolvedValue(overrides.networks ?? { networks: [] });
  vi.mocked(createNetwork).mockResolvedValue({ ok: true });
  vi.mocked(onboardingState).mockResolvedValue(overrides.onboarding ?? SETUP_STATE);
  vi.mocked(saveOnboarding).mockResolvedValue(overrides.onboarding ?? SETUP_STATE);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { unmount } = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/welcome"]}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );

  return { emit: emitLive, unmount };
}

/** Clicks through step 2 ("Which networks should Vault watch?"), which now
 *  sits between naming the site and the install command. */
async function passNetworks() {
  await screen.findByText(/which networks should Vault watch/i);
  await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
}

beforeEach(() => {
  listeners.clear();
  try {
    window.localStorage.clear();
  } catch {
    // some jsdom environments need no clearing at all
  }
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Onboarding", () => {
  it("walks a fresh tenant to the enrol command and advances on the first scan", async () => {
    const { emit } = renderPage(<Onboarding />);

    await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home");
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
    await passNetworks();

    await userEvent.click(await screen.findByRole("button", { name: /macos/i }));
    // The command the hub serves itself — `tests/install.test.tsx` is the
    // guard that it names no host but this app's origin.
    expect(await screen.findByText(/curl -fsSL .*\/install .* --code ABCD2345/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
    expect(screen.getByText(/waiting for the first scan/i)).toBeInTheDocument();

    emit({ type: "scan", ts: "2026-09-10T08:05:00Z", data: { score: 83, devices: 24, alerts: 9 } });

    expect(await screen.findByText(/here is what we found/i)).toBeInTheDocument();
  });

  // Nothing is auto-detected here (spec §5): a wrong guess about mirror vs.
  // router would show a customer their neighbour's traffic, so the wizard
  // asks instead of inferring, and defaults to the answer that assumes
  // nothing.
  it("asks how Vault sees this network's traffic, defaulting to the answer that assumes nothing", async () => {
    renderPage(<Onboarding />);
    await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home");
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
    await screen.findByText(/which networks should Vault watch/i);

    const select = screen.getByLabelText(/how does Vault see this network's traffic/i);
    expect(select).toHaveValue("router");
    expect(screen.getByRole("option", { name: /not sure — skip for now/i })).toBeInTheDocument();
  });

  it("sends traffic_mode: mirror when the operator says a mirror port feeds the sensor", async () => {
    renderPage(<Onboarding />);
    await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home");
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
    await screen.findByText(/which networks should Vault watch/i);

    await userEvent.selectOptions(
      screen.getByLabelText(/how does Vault see this network's traffic/i),
      "A mirror/SPAN port on the switch feeds the sensor",
    );
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));

    await waitFor(() =>
      expect(createNetwork).toHaveBeenCalledWith(expect.objectContaining({ traffic_mode: "mirror" })),
    );
  });

  it("sends traffic_mode: router when the operator leaves the mirror question unanswered", async () => {
    renderPage(<Onboarding />);
    await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home");
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
    await passNetworks();

    await waitFor(() =>
      expect(createNetwork).toHaveBeenCalledWith(expect.objectContaining({ traffic_mode: "router" })),
    );
  });

  it("saves the site name to the profile's company field so it survives a reload", async () => {
    renderPage(<Onboarding />);
    await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home");
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));

    await waitFor(() => expect(saveSettingsSection).toHaveBeenCalledWith("profile", { company: "Home" }));
  });

  it("shows an install command for each platform and mints the code once, up front", async () => {
    renderPage(<Onboarding />);
    await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home");
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));

    await passNetworks();

    await userEvent.click(await screen.findByRole("button", { name: /linux/i }));
    expect(await screen.findByText(/curl -fsSL .*\/install .* --code ABCD2345/)).toBeInTheDocument();

    // Docker is gone: no sensor image is published anywhere, so the tab that
    // used to point at `ghcr.io/guardian/sensor` has no honest destination.
    expect(screen.queryByRole("button", { name: /docker/i })).not.toBeInTheDocument();

    expect(generateEnrolCode).toHaveBeenCalledTimes(1);
  });

  it("resumes from what the hub says the tenant still needs", async () => {
    // Resume used to come from a step name in `localStorage`, which works on
    // one machine with one network. The hub now answers what is outstanding by
    // looking at the rows, so a wizard reopened on a phone lands in the right
    // place with no local state at all -- which is the only way a tenant with
    // three LANs at three different stages can be represented.
    renderPage(<Onboarding />, {
      onboarding: {
        ...SETUP_STATE,
        site_named: true,
        networks: [{
          id: "n1", name: "Clinic", slug: "clinic", sensor: "none",
          first_scan_at: "", devices: 0, by_group: {}, dns: "off",
        }],
        next: { network_id: "n1", action: "enrol_sensor" },
      },
    });
    expect(await screen.findByRole("button", { name: /macos/i })).toBeInTheDocument();
    expect(screen.queryByLabelText(/what should we call/i)).not.toBeInTheDocument();
  });

  it("ignores a stale step left in localStorage when the hub disagrees", async () => {
    // The failure this removes: a cursor saying "install a sensor" while a
    // sensor is already pushing scans. The rows win, always.
    window.localStorage.setItem("guardian.onboarding.t1", "sensor");
    renderPage(<Onboarding />, {
      onboarding: {
        ...SETUP_STATE,
        site_named: true,
        networks: [{
          id: "n1", name: "Clinic", slug: "clinic", sensor: "active",
          first_scan_at: "", devices: 0, by_group: {}, dns: "off",
        }],
        next: { network_id: "n1", action: "await_first_scan" },
      },
    });
    expect(await screen.findByText(/waiting for the first scan|first scan/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /macos/i })).not.toBeInTheDocument();
  });

  it("says a sensor has gone quiet rather than asking for another install", async () => {
    // "Never installed" and "installed, reported, then stopped" need opposite
    // advice. `remedy`'s human tier exists for the second: no fix Vault runs
    // reaches a host that is not answering.
    renderPage(<Onboarding />, {
      onboarding: {
        ...SETUP_STATE,
        site_named: true,
        networks: [{
          id: "n1", name: "Clinic", slug: "clinic", sensor: "quiet",
          first_scan_at: "", devices: 0, by_group: {}, dns: "off",
        }],
        next: { network_id: "n1", action: "await_first_scan" },
      },
    });
    expect(await screen.findByText(/stopped|quiet|not answering/i)).toBeInTheDocument();
  });

  it("summarises what the first scan found before asking anything of anyone", async () => {
    // The customer is looking at their own network for the first time. The
    // shape of it -- how many cameras, how many phones -- is the thing to lead
    // with; a list of things to fill in is not.
    renderPage(<Onboarding />, {
      onboarding: {
        ...SETUP_STATE,
        site_named: true,
        networks: [{
          id: "n1", name: "Clinic", slug: "clinic", sensor: "active",
          first_scan_at: "2026-09-19T09:00:00Z", devices: 23,
          by_group: { Cameras: 12, Phones: 8, Printers: 3 }, dns: "off",
        }],
        next: { network_id: "n1", action: "offer_dns" },
      },
    });
    expect(await screen.findByText(/12/)).toBeInTheDocument();
    expect(screen.getByText(/Cameras/i)).toBeInTheDocument();
  });

  it("can be finished having named nothing at all", async () => {
    // The regression worth pinning. Naming is an offer; a step that cannot be
    // completed without walking a list of 35 devices is the step people
    // abandon, and the baseline already keeps that estate quiet.
    const { emit } = renderPage(<Onboarding />, { devices: devicesWithUnverified });
    await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home");
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
    await passNetworks();
    await userEvent.click(await screen.findByRole("button", { name: /macos/i }));
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
    emit({ type: "scan", ts: "2026-09-19T09:00:00Z" } as LiveEvent);
    await screen.findByText(/here is what we found/i);
    const done = await screen.findByRole("button", { name: /^done$/i });
    expect(done).toBeEnabled();
  });

  it("offers DNS filtering beside the exit, and only while it is off", async () => {
    // The one setup step that needs a password the customer may not have.
    // Required, it hard-stops most practices on day one; absent, most never
    // find it. So: offered next to Done, skippable at no cost.
    const withDns = (dns: string) => ({
      ...SETUP_STATE,
      site_named: true,
      networks: [{
        id: "n1", name: "Clinic", slug: "clinic", sensor: "active",
        first_scan_at: "2026-09-19T09:00:00Z", devices: 4,
        by_group: { Cameras: 4 }, dns,
      }],
      next: { network_id: "n1", action: "offer_dns" },
    });

    const { unmount } = renderPage(<Onboarding />, { onboarding: withDns("off") });
    expect(await screen.findByRole("button", { name: /set up dns filtering/i })).toBeInTheDocument();
    unmount();

    // `dns: "working"` is the hub's live signal — traffic genuinely flowing
    // through the resolver — so the offer retires itself.
    renderPage(<Onboarding />, { onboarding: withDns("working") });
    await screen.findByText(/here is what we found/i);
    expect(screen.queryByRole("button", { name: /set up dns filtering/i })).not.toBeInTheDocument();
  });

  it("explains what Vault is doing during the wait", async () => {
    // The only minute in the product where the customer waits with nothing to
    // read. Each line is part of the scan they are waiting on, not a feature
    // list — it is true while they read it.
    renderPage(<Onboarding />, {
      onboarding: {
        ...SETUP_STATE,
        site_named: true,
        networks: [{
          id: "n1", name: "Clinic", slug: "clinic", sensor: "active",
          first_scan_at: "", devices: 0, by_group: {}, dns: "off",
        }],
        next: { network_id: "n1", action: "await_first_scan" },
      },
    });
    expect(await screen.findByText(/answers from the internet/i)).toBeInTheDocument();
    expect(screen.getByText(/what is normal here/i)).toBeInTheDocument();
  });

  it("lets a keyboard user trust or label an unverified device once the scan lands", async () => {
    const { emit } = renderPage(<Onboarding />, { devices: devicesWithUnverified });
    await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home");
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
    await passNetworks();
    await userEvent.click(await screen.findByRole("button", { name: /macos/i }));
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));

    emit({ type: "scan", ts: "2026-09-10T08:05:00Z", data: {} });
    await screen.findByText(/here is what we found/i);

    await userEvent.click(await screen.findByRole("button", { name: /^trust$/i }));
    await waitFor(() =>
      expect(updateDevice).toHaveBeenCalledWith("aa:bb:cc:00:00:01", { trusted: true }),
    );
  });

  it("does not mint an enrol code while still on step 1 (name)", async () => {
    renderPage(<Onboarding />);
    await screen.findByLabelText(/what should we call/i);

    expect(generateEnrolCode).not.toHaveBeenCalled();
  });

  it("has a Skip for now control on step 1 that records the skip for this user", async () => {
    renderPage(<Onboarding />);
    await screen.findByLabelText(/what should we call/i);

    await userEvent.click(screen.getByRole("button", { name: /skip for now/i }));

    // On the hub, not in `localStorage`: a colleague on the same site must
    // still be shown around, and this person must stay skipped on their other
    // machines.
    expect(saveOnboarding).toHaveBeenCalledWith({ skipped: true });
  });

  it("also offers Skip for now on the sensor step", async () => {
    renderPage(<Onboarding />);
    await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home");
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
    await passNetworks();
    await screen.findByRole("button", { name: /macos/i });

    await userEvent.click(screen.getByRole("button", { name: /skip for now/i }));

    expect(saveOnboarding).toHaveBeenCalledWith({ skipped: true });
  });

  it("shows the still-nothing troubleshooting list on request while waiting", async () => {
    renderPage(<Onboarding />);
    await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home");
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
    await passNetworks();
    await userEvent.click(await screen.findByRole("button", { name: /macos/i }));
    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));

    expect(screen.queryByText(/outbound internet access/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /still nothing/i }));
    expect(screen.getByText(/outbound internet access/i)).toBeInTheDocument();
  });

  // The second walkthrough: someone joining a site that already works. It is a
  // different wizard, not a shorter one — its whole job is orientation, and it
  // must never ask them to install or rename anything a colleague already did.
  describe("the orienting walkthrough", () => {
    it("welcomes a newcomer by site name and asks them to install nothing", async () => {
      renderPage(<Onboarding />, { onboarding: TOUR_STATE });

      expect(await screen.findByRole("heading", { name: /welcome to home/i })).toBeInTheDocument();
      expect(screen.getByText(/nothing to install/i)).toBeInTheDocument();
      // None of the installing path's steps appear.
      expect(screen.queryByLabelText(/what should we call/i)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /macos/i })).not.toBeInTheDocument();
      expect(generateEnrolCode).not.toHaveBeenCalled();
    });

    it("walks three screens to Done and records completion", async () => {
      renderPage(<Onboarding />, { onboarding: TOUR_STATE });
      await screen.findByRole("heading", { name: /welcome to home/i });

      await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
      expect(await screen.findByRole("heading", { name: /where the answers live/i })).toBeInTheDocument();
      // Every orientation point is a real in-app link, not a description of one.
      expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("href", "/overview");
      expect(screen.getByRole("link", { name: "Inbox" })).toHaveAttribute("href", "/inbox?filter=alerts");

      await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
      expect(await screen.findByRole("heading", { name: /when you need a hand/i })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /help section/i })).toHaveAttribute("href", "/help");

      await userEvent.click(screen.getByRole("button", { name: /^done$/i }));
      expect(saveOnboarding).toHaveBeenCalledWith({ completed: true });
    });

    it("keeps Skip for now", async () => {
      renderPage(<Onboarding />, { onboarding: TOUR_STATE });
      await screen.findByRole("heading", { name: /welcome to home/i });

      await userEvent.click(screen.getByRole("button", { name: /skip for now/i }));
      expect(saveOnboarding).toHaveBeenCalledWith({ skipped: true });
    });

    it("falls back to the installing path when the hub cannot answer", async () => {
      vi.mocked(onboardingState).mockRejectedValue(new Error("offline"));
      renderPage(<Onboarding />);

      expect(await screen.findByLabelText(/what should we call/i)).toBeInTheDocument();
    });
  });
});
