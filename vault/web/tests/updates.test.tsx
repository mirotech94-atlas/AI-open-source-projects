import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import Updates from "../src/pages/Updates";
import type { MeOut } from "../src/api";
import type { LiveEvent, UpdatesPayload } from "../src/types";

// --- fake live feed, controllable per test via `emit` -----------------------

const { fakeLive, emitLiveEvent } = vi.hoisted(() => {
  const listeners = new Map<string, Set<(event: LiveEvent) => void>>();
  const feed = {
    status: "open" as const,
    last: null as LiveEvent | null,
    on: (type: string, fn: (event: LiveEvent) => void) => {
      let set = listeners.get(type);
      if (!set) {
        set = new Set();
        listeners.set(type, set);
      }
      set.add(fn);
      return () => {
        set?.delete(fn);
      };
    },
  };
  const emit = (event: LiveEvent) => {
    for (const fn of listeners.get(event.type) ?? []) fn(event);
  };
  return { fakeLive: feed, emitLiveEvent: emit };
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
    updatesPayload: vi.fn(),
    queueStatus: vi.fn(),
    fixConfirmation: vi.fn((host: string, action: string, arg: string | null = null) => {
      postedCalls.push({ path: "/api/v1/fix/confirm", body: { host, action, arg } });
      return Promise.resolve({
        token: "tok-abc",
        expires: "2026-09-09T18:10:00-04:00",
        tier: 3,
        host,
        action,
        summary: `Run full apt upgrade (all packages) on ${host}. This is disruptive and cannot be undone from here.`,
      });
    }),
    requestFix: vi.fn((host: string, action: string, arg: string | null = null, token: string | null = null) => {
      postedCalls.push({ path: "/api/v1/fix", body: { host, action, arg, confirm_token: token } });
      return Promise.resolve({ id: "q-fix-1", status: "pending" });
    }),
    // The page asks through `requestFixOrConfirm`, which lives inside lib/api
    // and calls the module's own `requestFix` — so the flow is mocked at that
    // seam: the hub refuses a disruptive action (nothing queued) and hands
    // back the confirmation instead.
    requestFixOrConfirm: vi.fn(async (host: string, action: string, arg: string | null = null) => {
      if (action === "full_upgrade") {
        postedCalls.push({ path: "/api/v1/fix/confirm", body: { host, action, arg } });
        return {
          ack: null,
          confirmation: {
            token: "tok-abc",
            expires: "2026-09-09T18:10:00-04:00",
            tier: 3,
            host,
            action,
            summary: `Run full apt upgrade (all packages) on ${host}. This is disruptive and cannot be undone from here.`,
          },
        };
      }
      postedCalls.push({ path: "/api/v1/fix", body: { host, action, arg, confirm_token: null } });
      return { ack: { id: "q-fix-1", status: "pending" }, confirmation: null };
    }),
    planReboot: vi.fn((host: string, window: { start: string; end: string }) => {
      postedCalls.push({
        path: "/api/v1/reboot-plan",
        body: { host, window_start: window.start, window_end: window.end },
      });
      return Promise.resolve({ queue_id: "q-reboot-1" });
    }),
  };
});

import { updatesPayload } from "../src/lib/api";

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

export const updatesFixture: UpdatesPayload = {
  calendar: [
    { day: "2026-09-01", host: "vps1", patches: 3, reboots: 1, state: "warn" },
    { day: "2026-09-02", host: "vps1", patches: 0, reboots: 0, state: "ok" },
    { day: "2026-09-03", host: "vps1", patches: 5, reboots: 0, state: "warn" },
    { day: "2026-09-01", host: "mac", patches: 0, reboots: 0, state: "ok" },
    { day: "2026-09-02", host: "mac", patches: 2, reboots: 0, state: "ok" },
    { day: "2026-09-03", host: "mac", patches: 1, reboots: 1, state: "warn" },
  ],
  hosts: [
    {
      checked: true,
      name: "vps1",
      os: "Debian 13",
      kernel: "6.6.0",
      kernel_latest: "6.6.2",
      pending: 6,
      security_pending: 1,
      cve_fixable: 2,
      unattended: true,
      reboot_required: true,
      last_apt: "2026-09-09T04:00:00-04:00",
      packages: ["openssl", "curl", "libc6", "nginx"],
      security_packages: ["openssl"],
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
      last_apt: "2026-09-08T09:00:00-04:00",
      packages: [],
      security_packages: [],
    },
  ],
  cves: [
    {
      cve: "CVE-2026-1001",
      package: "openssl",
      urgency: "critical",
      host: "vps1",
      fixable: true,
      fixed_on: "",
      tracker: "https://example.com/cve-2026-1001",
    },
    {
      cve: "CVE-2026-1002",
      package: "curl",
      urgency: "high",
      host: "vps1",
      fixable: true,
      fixed_on: "",
      tracker: "https://example.com/cve-2026-1002",
    },
    {
      cve: "CVE-2026-1003",
      package: "sqlite3",
      urgency: "medium",
      host: "vps1",
      fixable: false,
      fixed_on: "2026-09-05",
      tracker: "",
    },
    {
      cve: "CVE-2026-1004",
      package: "zlib",
      urgency: "low",
      host: "mac",
      fixable: false,
      fixed_on: "2026-09-01",
      tracker: "",
    },
  ],
  progress: [
    {
      queue_id: "q-up1",
      host: "vps1",
      action: "apt upgrade",
      status: "running",
      done: 10,
      total: 40,
      log_tail: "Reading package lists...\nBuilding dependency tree...\nUpgrading openssl...",
      started: "2026-09-09T18:00:00-04:00",
      finished: null,
    },
  ],
  posture: { pending: 0, security_pending: 0, needs_reboot: 0, servers_checked: 0, servers_unchecked: 0, unmeasured: 0 },
  reboot_plan: {
    hosts: [
      {
        host: "vps1",
        reason: "Kernel update pending — a reboot applies 6.6.2.",
        services: ["nginx", "guardian-hub"],
        queued: false,
      },
    ],
    windows: [
      { label: "Tonight 02:00–05:00", start: "02:00", end: "05:00" },
      { label: "Sunday 03:00–06:00", start: "03:00", end: "06:00" },
    ],
  },
  mac: {
    os: "15.6",
    available: ["macOS Sequoia 15.6.1", "Safari 18.6"],
    checked: "2026-09-09T06:00:00-04:00",
    error: "",
    reported: true,
  },
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
  updates?: UpdatesPayload;
  me?: MeOut;
};

function renderPage(element: ReactElement, overrides: Overrides = {}) {
  vi.mocked(updatesPayload).mockResolvedValue(overrides.updates ?? updatesFixture);
  vi.mocked(me).mockResolvedValue(overrides.me ?? OPERATOR_ME);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>{element}</ToastProvider>
    </QueryClientProvider>,
  );

  return {
    emit: emitLiveEvent,
    posted,
  };
}

describe("Updates", () => {
  it("renders one calendar cell per hub row and marks reboots", async () => {
    renderPage(<Updates />, { updates: updatesFixture });
    expect(await screen.findAllByTestId("patch-cell")).toHaveLength(updatesFixture.calendar.length);
    expect(screen.getAllByTestId("reboot-mark")).toHaveLength(
      updatesFixture.calendar.filter((c) => c.reboots > 0).length,
    );
  });

  it("filters the CVE table by severity", async () => {
    renderPage(<Updates />, { updates: updatesFixture });
    // "Still open" is the default, so start from "any state" to count all.
    await screen.findAllByTestId("cve-row");
    await userEvent.selectOptions(screen.getByLabelText("Filter by state"), "all");
    const all = screen.getAllByTestId("cve-row").length;
    await userEvent.selectOptions(screen.getByLabelText("Filter by severity"), "critical");
    expect(screen.getAllByTestId("cve-row").length).toBeLessThan(all);
  });

  it("filters the CVE table by server and by state, not only by name", async () => {
    // Item 32: severity, which server, and whether it is still outstanding
    // are the three questions a person arrives with.
    renderPage(<Updates />, { updates: updatesFixture });
    await screen.findAllByTestId("cve-row");
    await userEvent.selectOptions(screen.getByLabelText("Filter by state"), "all");
    const all = screen.getAllByTestId("cve-row").length;

    const serverPicker = screen.getByLabelText("Filter by server");
    const firstServer = updatesFixture.cves[0].host;
    await userEvent.selectOptions(serverPicker, firstServer);
    const forOne = screen.getAllByTestId("cve-row").length;
    expect(forOne).toBeGreaterThan(0);
    expect(forOne).toBeLessThanOrEqual(all);

    await userEvent.selectOptions(serverPicker, "all");
    await userEvent.selectOptions(screen.getByLabelText("Filter by state"), "fixable");
    for (const row of screen.getAllByTestId("cve-row")) {
      expect(row).not.toHaveTextContent(/\(fixed\)/);
    }
  });

  it("advances the upgrade bar from a live frame", async () => {
    const { emit } = renderPage(<Updates />, { updates: updatesFixture });
    await screen.findAllByTestId("patch-cell");

    emit({
      type: "upgrade",
      ts: "2026-09-09T18:05:00-04:00",
      data: { queue_id: updatesFixture.progress[0].queue_id, host: "prod", done: 30, total: 40 },
    });

    expect(await screen.findByRole("progressbar")).toHaveAttribute("aria-valuenow", "30");
  });

  it("schedules a reboot in the chosen window", async () => {
    const { posted } = renderPage(<Updates />, { updates: updatesFixture, me: ADMIN_ME });
    const windowSelect = await screen.findByLabelText(/window/i);
    await waitFor(() => expect(windowSelect).toBeEnabled());
    await userEvent.selectOptions(windowSelect, "Tonight 02:00–05:00");
    const button = await screen.findByRole("button", { name: /schedule reboot/i });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);

    expect(posted("/api/v1/reboot-plan")).toMatchObject({ host: "vps1", window_start: "02:00", window_end: "05:00" });
  });

  it("runs a reversible security update on one press, with no confirmation step", async () => {
    const { posted } = renderPage(<Updates />, { updates: updatesFixture, me: ADMIN_ME });
    const button = await screen.findByRole("button", { name: /security now/i });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);

    expect(posted("/api/v1/fix")).toMatchObject({
      host: "vps1",
      action: "security_updates",
      confirm_token: null,
    });
    expect(posted("/api/v1/fix/confirm")).toBeUndefined();
  });

  it("never runs a full upgrade on a bare click — it asks the hub, shows its sentence, and spends the token", async () => {
    const { posted } = renderPage(<Updates />, { updates: updatesFixture, me: ADMIN_ME });
    const button = await screen.findByRole("button", { name: /upgrade all/i });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);

    // Pressing the row button mints a confirmation and queues nothing.
    expect(posted("/api/v1/fix/confirm")).toMatchObject({ host: "vps1", action: "full_upgrade" });
    expect(posted("/api/v1/fix")).toBeUndefined();

    // The dialog carries the hub's own summary, not copy written in the page.
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent(/disruptive/i);
    await userEvent.click(within(dialog).getByRole("button", { name: "Upgrade all" }));

    expect(posted("/api/v1/fix")).toMatchObject({
      host: "vps1",
      action: "full_upgrade",
      confirm_token: "tok-abc",
    });
  });

  it("cancelling the confirmation queues nothing", async () => {
    const { posted } = renderPage(<Updates />, { updates: updatesFixture, me: ADMIN_ME });
    const button = await screen.findByRole("button", { name: /upgrade all/i });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));

    expect(posted("/api/v1/fix")).toBeUndefined();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("names the packages waiting on each host and marks the security ones", async () => {
    renderPage(<Updates />, { updates: updatesFixture });
    await screen.findAllByTestId("patch-cell");
    expect(screen.getByText("libc6")).toBeInTheDocument();
    expect(screen.getByText("openssl", { selector: ".g-pkglist-names span" })).toHaveClass("g-tone-warn");
    // `pending` is 6 and only 4 names arrived, so the trim is stated.
    expect(screen.getByText(/and 2 more/)).toBeInTheDocument();
  });

  it("shows what the sensor's own machine is waiting for, and who installs it", async () => {
    renderPage(<Updates />, { updates: updatesFixture });
    await screen.findAllByTestId("patch-cell");
    expect(screen.getByText("Safari 18.6")).toBeInTheDocument();
    expect(screen.getByText(/never applies an operating-system update/i)).toBeInTheDocument();
  });

  it("disables the apt row actions for an operator — the hub gates them admin", async () => {
    renderPage(<Updates />, { updates: updatesFixture, me: OPERATOR_ME });
    expect(await screen.findByRole("button", { name: /upgrade all/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /security now/i })).toBeDisabled();
  });

  it("disables the schedule button for a viewer", async () => {
    renderPage(<Updates />, { updates: updatesFixture, me: viewerMe });
    expect(await screen.findByRole("button", { name: /schedule reboot/i })).toBeDisabled();
  });

  it("disables the reboot planner for an operator, with an admin-role tooltip", async () => {
    renderPage(<Updates />, { updates: updatesFixture, me: OPERATOR_ME });
    const button = await screen.findByRole("button", { name: /schedule reboot/i });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "admin role required");
  });

  it("enables the reboot planner for an admin", async () => {
    renderPage(<Updates />, { updates: updatesFixture, me: ADMIN_ME });
    const button = await screen.findByRole("button", { name: /schedule reboot/i });
    await waitFor(() => expect(button).toBeEnabled());
  });
});
