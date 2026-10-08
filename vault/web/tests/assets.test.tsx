import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import Assets from "../src/pages/Assets";
import type { DeviceDetail, DevicesPayload, ScreenTimePayload } from "../src/types";

// --- fake live feed: Assets subscribes to the "device" frame to invalidate
// its device query, but no test here needs it to fire ------------------------

const { fakeLive } = vi.hoisted(() => {
  // Emittable: item 13 is about what happens when a sweep lands, so a test
  // has to be able to land one.
  const handlers = new Map<string, Set<(event: unknown) => void>>();
  const feed = {
    status: "open" as const,
    last: null,
    on: (type: string, fn: (event: unknown) => void) => {
      const set = handlers.get(type) ?? new Set();
      set.add(fn);
      handlers.set(type, set);
      return () => set.delete(fn);
    },
    emit: (type: string, event: unknown = {}) => {
      for (const fn of handlers.get(type) ?? []) fn(event);
    },
    reset: () => handlers.clear(),
  };
  return { fakeLive: feed };
});

vi.mock("../src/lib/live", () => ({
  useLiveFeed: () => fakeLive,
}));

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
    sitesPayload: vi.fn(),
    hostsPayload: vi.fn(),
    devicesPayload: vi.fn(),
    deviceDetail: vi.fn(),
    screenTimePayload: vi.fn(),
    settingsPayload: vi.fn(),
    // `updateDevice` is the ONLY device write route: the hub's `DeviceUpdate`
    // body accepts label/trusted/always_on/quarantined/parent all on
    // `POST /api/v1/devices/{mac}` (see `hub/guardian_hub/routers/write.py`).
    // There is no `/quarantine` or `/merge` sub-route — a prior draft of this
    // drawer invented both; this mock (and the tests below) assert the real
    // path + body instead.
    updateDevice: vi.fn((mac: string, patch: Record<string, unknown>) => {
      postedCalls.push({ path: `/api/v1/devices/${mac}`, body: patch });
      return Promise.resolve({});
    }),
    forgetDevice: vi.fn((mac: string) => {
      postedCalls.push({ path: `/api/v1/devices/${mac}/forget`, body: {} });
      return Promise.resolve({});
    }),
  };
});

// Default role is "operator" — enough to exercise every write control except
// the admin-only Forget button, matching most tests below which don't care
// about role gating. Tests that do (viewer-vs-operator) override with
// `mockResolvedValueOnce`.
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

import {
  devicesPayload,
  deviceDetail,
  hostsPayload,
  screenTimePayload,
  sitesPayload,
  updateDevice,
} from "../src/lib/api";
import { me } from "../src/api";
import { NO_TILES } from "./tiles";

afterEach(() => {
  postedCalls.length = 0;
  vi.clearAllMocks();
  // `mockReset` (not just `clearAllMocks`) restores the factory's own
  // operator-role implementation, so the viewer-role case below can override
  // it with `mockResolvedValue` — which it has to, since `Assets` and the
  // drawer are now two observers of the same `["me"]` query and a one-shot
  // mock would leave the second one unanswered.
  vi.mocked(me).mockReset();
});

// --- fixtures ----------------------------------------------------------------

function device(overrides: Partial<DevicesPayload["groups"][number]["devices"][number]>) {
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
    network: null,
    ...overrides,
  };
}

const routerDevice = device({
  mac: "58:96:71:61:05:e3",
  ip: "192.168.1.1",
  name: "router",
  label: "Router",
  group: "network",
  trusted: true,
});

const devicesFixture: DevicesPayload = {
  groups: [
    { group: "camera", devices: [device({ mac: "aa:aa:aa:aa:aa:01", name: "cam-1", group: "camera" })], network: null },
    {
      group: "computer",
      devices: [
        device({ mac: "aa:aa:aa:aa:aa:02", name: "laptop-1", group: "computer" }),
        device({ mac: "aa:aa:aa:aa:aa:03", name: "laptop-2", group: "computer" }),
      ],
      network: null,
    },
    { group: "network", devices: [routerDevice], network: null },
  ],
  chips: [
    { group: "camera", count: 1, online: 1, network_id: null },
    { group: "computer", count: 2, online: 1, network_id: null },
    { group: "network", count: 1, online: 1, network_id: null },
  ],
  total: 4,
  online: 3,
  unverified: 0,
  cameras: 0,
  cameras_online: 0,
  tiles: NO_TILES,
  networks: [],
  charts: [],
};

const deviceDetailFixture: DeviceDetail = {
  device: routerDevice,
  timeline: [],
  presence: {
    online_pct_24h: 100,
    online_pct_7d: 99,
    hours: [],
    typical_hours: [],
    days: [],
    strip: [],
  },
  ports: [],
  dns: { queries_24h: 0, blocked_24h: 0, top: [], blocked_top: [] },
  alerts: [],
  children: [],
  events: [],
  familiarity: { mac: "", name: "", label: "", ip: "", vendor: "", randomized_mac: false, trusted: false, verdict: "unknown", summary: "", reasons: [], days_seen: 0, lookback_days: 30, span_days: 0, first_seen: "", last_seen: "", silent_days: null, overnight_share: 0, typical_hours: [], joins: 0, leaves: 0, dns_queries: 0, top_dns: [], platform_hint: "", name_siblings: [], other_networks: [] },
};

const presenceFixture: ScreenTimePayload = {
  days: 7,
  scans: 400,
  devices: [
    {
      mac: "58:96:71:61:05:e3",
      name: "Router",
      ip: "192.168.1.1",
      trusted: true,
      total_h: 168,
      pct: 100,
      hours_per_day: 24,
      hours: [24, 24, 24],
      network: null,
    },
    {
      mac: "aa:aa:aa:aa:aa:02",
      name: "laptop-1",
      ip: "192.168.1.10",
      trusted: false,
      total_h: 40,
      pct: 24,
      hours_per_day: 6,
      hours: [5, 6, 7],
      network: null,
    },
  ],
  heatmap: [
    { row: "58:96:71:61:05:e3", col: 0, value: 100 },
    { row: "58:96:71:61:05:e3", col: 1, value: 90 },
    { row: "58:96:71:61:05:e3", col: 2, value: 80 },
    { row: "aa:aa:aa:aa:aa:02", col: 0, value: 10 },
    { row: "aa:aa:aa:aa:aa:02", col: 1, value: 20 },
    { row: "aa:aa:aa:aa:aa:02", col: 2, value: 30 },
  ],
};

// --- helpers -------------------------------------------------------------

function renderAssets(route: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[route]}>
          <Routes>
            <Route path="/assets" element={<Assets />} />
            <Route path="/assets/devices/:mac" element={<Assets />} />
            <Route path="/assets/:view" element={<Assets />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("Assets", () => {
  it("shows devices as cards only, with no table view to toggle to", async () => {
    // A device is a thing with a name, an owner and a picture of what it is.
    // A row of cells says none of that, so there is one view, not two.
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);
    renderAssets("/assets/devices");
    await screen.findByRole("tab", { name: "Devices" });
    expect(screen.queryByRole("tab", { name: "Table" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Cards" })).toBeNull();
    expect(screen.queryByTestId("devices-table")).not.toBeInTheDocument();
  });

  it("has no Everything tab over the four kinds of thing it lists", async () => {
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);
    renderAssets("/assets/devices");
    await screen.findByRole("tab", { name: "Devices" });
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent ?? "");
    expect(tabs).toEqual(["Websites", "Servers", "Devices", "Network presence"]);
  });

  it("breaks the devices down by vendor, largest first, Unknown last", async () => {
    const vendored: DevicesPayload = {
      ...devicesFixture,
      groups: [
        {
          group: "computer",
          devices: [
            device({ mac: "aa:aa:aa:aa:aa:11", vendor: "Apple" }),
            device({ mac: "aa:aa:aa:aa:aa:12", vendor: "Apple" }),
            device({ mac: "aa:aa:aa:aa:aa:13", vendor: "Apple" }),
            device({ mac: "aa:aa:aa:aa:aa:14", vendor: "Espressif" }),
            device({ mac: "aa:aa:aa:aa:aa:15", vendor: "" }),
          ],
          network: null,
        },
      ],
    };
    vi.mocked(devicesPayload).mockResolvedValue(vendored);

    renderAssets("/assets/devices");

    const rows = await screen.findByTestId("vendor-donut");
    const labels = [...rows.querySelectorAll(".g-vendor-legend .g-vendor-name")].map((n) => n.textContent);
    // "Not identified" is a bucket you read past, so it sorts last however
    // big it is -- and it says what it means rather than "Unknown".
    expect(labels).toEqual(["Apple", "Espressif", "Not identified"]);
    const counts = [...rows.querySelectorAll(".g-vendor-legend .g-vendor-count")].map((n) => n.textContent);
    expect(counts).toEqual(["3", "1", "1"]);
    // Identity is never colour alone: each slice is named in the legend and
    // the whole figure has a text alternative.
    expect(rows.querySelector("svg")).toHaveAccessibleName(/Devices by manufacturer/);
  });

  it("names a card by its vendor when the device has no hostname", async () => {
    // `DevicesTable` renders label -> name -> vendor -> mac and the card grid
    // skipped vendor, so the same nameless printer read "Brother" in the table
    // and "aa:bb:cc:dd:ee:ff" on a card. The cards are what a new customer
    // sees first, which made a freshly discovered estate look like a list of
    // hex rather than a list of things.
    const nameless: DevicesPayload = {
      ...devicesFixture,
      groups: [
        {
          group: "printer",
          devices: [device({ mac: "aa:bb:cc:dd:ee:ff", name: "", label: "", vendor: "Brother" })],
          network: null,
        },
      ],
    };
    vi.mocked(devicesPayload).mockResolvedValue(nameless);

    renderAssets("/assets/devices");

    // Scoped to the CARD's own name element. A bare findByText passes without
    // the fix, because the vendor-breakdown bars on the same page also render
    // the word "Brother" -- a green that proves nothing about the card.
    const cards = await screen.findAllByTestId("device-card");
    const names = cards.map((c) => c.querySelector(".g-device-name")?.textContent?.trim());
    expect(names).toEqual(["Brother"]);
  });

  it("falls back to the MAC only when there is no vendor either", async () => {
    // The last resort stays the last resort: a device with nothing to call it
    // by must still be identifiable, not blank.
    const anonymous: DevicesPayload = {
      ...devicesFixture,
      groups: [
        {
          group: "unknown",
          devices: [device({ mac: "aa:bb:cc:dd:ee:fe", name: "", label: "", vendor: "" })],
          network: null,
        },
      ],
    };
    vi.mocked(devicesPayload).mockResolvedValue(anonymous);

    renderAssets("/assets/devices");

    const cards = await screen.findAllByTestId("device-card");
    const names = cards.map((c) => c.querySelector(".g-device-name")?.textContent?.trim());
    expect(names).toEqual(["aa:bb:cc:dd:ee:fe"]);
  });

  it("collapses the vendor tail into one Other row", async () => {
    const many: DevicesPayload = {
      ...devicesFixture,
      groups: [
        {
          group: "computer",
          devices: Array.from({ length: 12 }, (_, i) =>
            device({ mac: `aa:aa:aa:aa:bb:${String(i).padStart(2, "0")}`, vendor: `Vendor${i}` }),
          ),
          network: null,
        },
      ],
    };
    vi.mocked(devicesPayload).mockResolvedValue(many);

    renderAssets("/assets/devices");

    const rows = await screen.findByTestId("vendor-donut");
    const labels = [...rows.querySelectorAll(".g-vendor-legend .g-vendor-name")].map((n) => n.textContent);
    // Five named + Other: five is the categorical palette's size, and a
    // sixth hue would have to be generated -- indistinguishable under
    // colour-blindness, which is the one thing a legend cannot rescue.
    expect(labels).toHaveLength(6);
    expect(labels[5]).toBe("Other");
    expect([...rows.querySelectorAll(".g-vendor-legend .g-vendor-count")].at(-1)?.textContent).toBe("7");
  });

  it("filters devices by kind chip", async () => {
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);

    renderAssets("/assets/devices");

    const before = (await screen.findAllByTestId("device-card")).length;
    expect(before).toBe(4);

    await userEvent.click(screen.getByRole("button", { name: /Cameras/ }));

    expect(screen.getAllByTestId("device-card").length).toBeLessThan(before);
    expect(screen.getAllByTestId("device-card")).toHaveLength(1);
  });

  it("opens the drawer from a deep link and saves a label", async () => {
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);
    vi.mocked(deviceDetail).mockResolvedValue(deviceDetailFixture);

    renderAssets("/assets/devices/58:96:71:61:05:e3");

    expect(await screen.findByRole("dialog", { name: /Router/ })).toBeInTheDocument();

    const input = screen.getByLabelText("Label");
    await userEvent.clear(input);
    await userEvent.type(input, "Front door router{Enter}");

    expect(posted("/api/v1/devices/58:96:71:61:05:e3")).toMatchObject({
      label: "Front door router",
    });
  });

  it("toggles quarantine from the drawer via the real device route", async () => {
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);
    vi.mocked(deviceDetail).mockResolvedValue(deviceDetailFixture);

    renderAssets("/assets/devices/58:96:71:61:05:e3");

    await screen.findByRole("dialog", { name: /Router/ });
    await userEvent.click(screen.getByRole("button", { name: "Quarantine" }));

    // Quarantine is not its own route — it's `POST /api/v1/devices/{mac}`
    // with `{ quarantined }`, same as label/trusted/always_on.
    expect(posted("/api/v1/devices/58:96:71:61:05:e3")).toMatchObject({
      quarantined: true,
    });
  });

  it("draws the screen-time heatmap from hub cells only", async () => {
    vi.mocked(screenTimePayload).mockResolvedValue(presenceFixture);

    renderAssets("/assets/screen-time");

    expect(await screen.findAllByTestId("heat-cell")).toHaveLength(presenceFixture.heatmap.length);
  });

  it("applies a device write optimistically before the request resolves", async () => {
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);
    vi.mocked(deviceDetail).mockResolvedValue(deviceDetailFixture);

    let resolveWrite: ((value: Record<string, unknown>) => void) | undefined;
    vi.mocked(updateDevice).mockImplementationOnce((mac, patch) => {
      postedCalls.push({ path: `/api/v1/devices/${mac}`, body: patch });
      return new Promise((resolve) => {
        resolveWrite = resolve;
      });
    });

    renderAssets("/assets/devices/58:96:71:61:05:e3");
    await screen.findByRole("dialog", { name: /Router/ });

    const trustedButton = screen.getByRole("button", { name: "Trusted" });
    expect(trustedButton).toHaveAttribute("aria-pressed", "true"); // routerDevice starts trusted

    await userEvent.click(trustedButton);

    // Flips immediately — the request above has not resolved yet.
    expect(trustedButton).toHaveAttribute("aria-pressed", "false");

    resolveWrite?.({});
    await waitFor(() => expect(trustedButton).toHaveAttribute("aria-pressed", "false"));
  });

  it("rolls back an optimistic device write and toasts on failure", async () => {
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);
    vi.mocked(deviceDetail).mockResolvedValue(deviceDetailFixture);
    vi.mocked(updateDevice).mockImplementationOnce((mac, patch) => {
      postedCalls.push({ path: `/api/v1/devices/${mac}`, body: patch });
      return Promise.reject(new Error("write failed"));
    });

    renderAssets("/assets/devices/58:96:71:61:05:e3");
    await screen.findByRole("dialog", { name: /Router/ });

    const trustedButton = screen.getByRole("button", { name: "Trusted" });
    expect(trustedButton).toHaveAttribute("aria-pressed", "true");

    // The click flips it optimistically and then, once the rejected write
    // settles, rolls it back to the pre-write value with an error toast —
    // by the time `userEvent.click` resolves, a same-tick rejection has
    // already run both halves, so only the settled (rolled-back) state is
    // observable here; the optimistic-then-pending case is covered by the
    // "applies a device write optimistically" test above.
    await userEvent.click(trustedButton);

    await waitFor(() => expect(trustedButton).toHaveAttribute("aria-pressed", "true"));
    expect(await screen.findByRole("status")).toHaveTextContent("write failed");
  });

  it("disables device write controls for a viewer", async () => {
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);
    vi.mocked(deviceDetail).mockResolvedValue(deviceDetailFixture);
    // `mockResolvedValue`, not `...Once`: `Assets` itself now calls `useMe()`
    // (to scope the selected network per tenant) alongside the drawer, and a
    // second observer joining a stale `["me"]` query triggers one background
    // refetch — which a one-shot mock would answer with the default operator.
    vi.mocked(me).mockResolvedValue({
      user: { username: "v", display_name: "Viewer", is_superadmin: false },
      tenants: [{ id: "t1", slug: "t1", name: "T1", role: "viewer" }],
      active_tenant: { id: "t1", slug: "t1", name: "T1", role: "viewer" },
      chat_providers: [],
    });

    renderAssets("/assets/devices/58:96:71:61:05:e3");
    await screen.findByRole("dialog", { name: /Router/ });

    expect(await screen.findByRole("button", { name: "Trusted" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Always-on" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Quarantine" })).toBeDisabled();
    expect(screen.getByLabelText("Label")).toBeDisabled();
  });

  it("enables device write controls for an operator", async () => {
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);
    vi.mocked(deviceDetail).mockResolvedValue(deviceDetailFixture);
    // default `me()` mock resolves role "operator"

    renderAssets("/assets/devices/58:96:71:61:05:e3");
    await screen.findByRole("dialog", { name: /Router/ });

    expect(await screen.findByRole("button", { name: "Trusted" })).toBeEnabled();
    expect(screen.getByLabelText("Label")).toBeEnabled();
    // Operator gets writes but not the admin-only Forget button.
    expect(screen.queryByRole("button", { name: "Forget device" })).not.toBeInTheDocument();
  });

  it("says why a device was filed as a camera, and says nothing on anything else", async () => {
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);
    vi.mocked(deviceDetail).mockResolvedValue({
      ...deviceDetailFixture,
      device: device({ mac: "aa:aa:aa:aa:aa:01", name: "cam-1", label: "Driveway", kind: "camera",
                       group: "Cameras",
                       online_7d: null,
                       camera_reasons: ["camera vendor (Reolink)", "answers on 554 (rtsp)"] }),
    });
    renderAssets("/assets/devices/aa:aa:aa:aa:aa:01");
    await screen.findByRole("dialog", { name: /Driveway/ });
    expect(screen.getByTestId("camera-reasons")).toHaveTextContent("answers on 554 (rtsp)");
  });

  it("falls back to how it was recognised when the sensor gave no reasons", async () => {
    // A camera the hub's own text heuristics classified (an imported tenant, or
    // a sensor that predates the classifier) carries no reasons: the drawer
    // must not render an empty line.
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);
    vi.mocked(deviceDetail).mockResolvedValue({
      ...deviceDetailFixture,
      device: device({ mac: "aa:aa:aa:aa:aa:01", label: "Driveway", kind: "camera",
                       group: "Cameras", camera_reasons: [] }),
    });
    renderAssets("/assets/devices/aa:aa:aa:aa:aa:01");
    await screen.findByRole("dialog", { name: /Driveway/ });
    expect(screen.getByTestId("camera-reasons")).toHaveTextContent("recognised from its name and maker");
  });

  it("shows no camera line for a device that is not a camera", async () => {
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);
    vi.mocked(deviceDetail).mockResolvedValue(deviceDetailFixture);
    renderAssets("/assets/devices/58:96:71:61:05:e3");
    await screen.findByRole("dialog", { name: /Router/ });
    expect(screen.queryByTestId("camera-reasons")).not.toBeInTheDocument();
  });

  it("shows a Split control for a merged device and splits it via the device route", async () => {
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);
    vi.mocked(deviceDetail).mockResolvedValue({
      ...deviceDetailFixture,
      device: { ...routerDevice, parent: "aa:aa:aa:aa:aa:02" },
    });

    renderAssets("/assets/devices/58:96:71:61:05:e3");
    await screen.findByRole("dialog", { name: /Router/ });

    expect(screen.getByText(/Merged into laptop-1/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Split" }));

    expect(posted("/api/v1/devices/58:96:71:61:05:e3")).toMatchObject({ parent: null });
  });
});

describe("the glance figures do not sit frozen (item 13)", () => {
  it("re-reads websites when a sweep lands", async () => {
    // Sites and servers had neither a live handler nor a poll: a tab left
    // open showed a certificate's remaining days as they were when it was
    // opened, however long ago that was.
    vi.mocked(sitesPayload).mockResolvedValue({ sites: [], charts: [] });
    renderAssets("/assets/websites");
    await waitFor(() => expect(vi.mocked(sitesPayload)).toHaveBeenCalled());
    const before = vi.mocked(sitesPayload).mock.calls.length;

    act(() => fakeLive.emit("scan"));

    await waitFor(() =>
      expect(vi.mocked(sitesPayload).mock.calls.length).toBeGreaterThan(before),
    );
  });

  it("re-reads servers when a sweep lands", async () => {
    vi.mocked(hostsPayload).mockResolvedValue({ hosts: [] });
    renderAssets("/assets/servers");
    await waitFor(() => expect(vi.mocked(hostsPayload)).toHaveBeenCalled());
    const before = vi.mocked(hostsPayload).mock.calls.length;

    act(() => fakeLive.emit("scan"));

    await waitFor(() =>
      expect(vi.mocked(hostsPayload).mock.calls.length).toBeGreaterThan(before),
    );
  });
});

describe("Network presence opens a device without leaving (item 14)", () => {
  it("keeps the Network presence tab selected when a row is clicked", async () => {
    // Routing to /assets/devices/<mac> also switches the sub-view, so a
    // client reading screen time was thrown onto the Devices tab and had to
    // navigate back. A drawer is an overlay, not a destination.
    vi.mocked(screenTimePayload).mockResolvedValue({
      days: 7,
      scans: 100,
      devices: [{ mac: "aa:aa:aa:aa:aa:11", name: "laptop", ip: "192.168.1.20", trusted: true,
                  total_h: 12, pct: 40, hours_per_day: 1.7, hours: [1, 2, 3], network: null }],
      heatmap: [],
    });
    vi.mocked(devicesPayload).mockResolvedValue(devicesFixture);
    renderAssets("/assets/screen-time");

    const presenceTab = await screen.findByRole("tab", { name: "Network presence" });
    expect(presenceTab).toHaveAttribute("aria-selected", "true");

    await userEvent.click(await screen.findByText("laptop"));

    // Still on Network presence — the drawer is an overlay over this tab.
    expect(screen.getByRole("tab", { name: "Network presence" }))
      .toHaveAttribute("aria-selected", "true");
  });
});

describe("a device card says how it was identified (item 15)", () => {
  it("distinguishes a name you gave it from a bare MAC nobody has identified", async () => {
    vi.mocked(devicesPayload).mockResolvedValue({
      ...devicesFixture,
      groups: [{
        group: "computer",
        devices: [
          device({ mac: "aa:aa:aa:aa:aa:01", label: "staff-phone", name: "", vendor: "Apple" }),
          device({ mac: "aa:aa:aa:aa:aa:02", label: "", name: "ipad", vendor: "Apple" }),
          device({ mac: "aa:aa:aa:aa:aa:03", label: "", name: "", vendor: "Espressif" }),
          device({ mac: "aa:aa:aa:aa:aa:04", label: "", name: "", vendor: "" }),
        ],
        network: null,
      }],
    });
    renderAssets("/assets/devices");
    await screen.findByText("staff-phone");
    // The unidentified one is the row to look at on a security product, so
    // it must not read the same as a device you have named yourself.
    expect(screen.getByText("named")).toBeInTheDocument();
    expect(screen.getByText("hostname")).toBeInTheDocument();
    expect(screen.getByText("maker only")).toBeInTheDocument();
    expect(screen.getByText("unidentified")).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// Item 16: which devices the grid is showing
// --------------------------------------------------------------------------

describe("device lens", () => {
  it("narrows the grid to the devices answering right now", async () => {
    renderAssets("/assets/devices");
    await screen.findByRole("group", { name: "Show which devices" });
    const lens = screen.getByRole("group", { name: "Show which devices" });
    const online = within(lens).getByRole("button", { name: /^Online · \d+$/ });
    await userEvent.click(online);
    expect(online.getAttribute("aria-pressed")).toBe("true");
  });

  it("counts each lens over the whole list, not the current view", async () => {
    renderAssets("/assets/devices");
    const lens = await screen.findByRole("group", { name: "Show which devices" });
    const before = within(lens).getByRole("button", { name: /^All · \d+$/ }).textContent;
    await userEvent.click(within(lens).getByRole("button", { name: /^Unidentified · \d+$/ }));
    // The number beside a button says how many pressing it would show —
    // so filtering must not make every other count collapse to zero.
    expect(within(lens).getByRole("button", { name: /^All · \d+$/ }).textContent).toBe(before);
  });
});
