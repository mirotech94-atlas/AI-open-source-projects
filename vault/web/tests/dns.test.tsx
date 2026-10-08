import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import Dns from "../src/pages/Dns";
import type { MeOut } from "../src/api";
import type { DnsPayload, LiveEvent } from "../src/types";

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

const { useLiveFeedMock } = vi.hoisted(() => ({ useLiveFeedMock: vi.fn() }));

vi.mock("../src/lib/live", () => ({
  useLiveFeed: (...args: unknown[]) => {
    useLiveFeedMock(...args);
    return fakeLive;
  },
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
    dnsPayload: vi.fn(),
    deviceDetail: vi.fn(),
    writeDnsRule: vi.fn((kind: "allow" | "block", domain: string, op: "add" | "remove") => {
      postedCalls.push({ path: "/api/v1/dns/rules", body: { kind, domain, op } });
      return Promise.resolve({});
    }),
    saveConfig: vi.fn((body: unknown) => {
      postedCalls.push({ path: "/api/v1/config", body });
      return Promise.resolve({});
    }),
  };
});

import { dnsPayload } from "../src/lib/api";

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

export const dnsFixture: DnsPayload = {
  status: {
    enabled: true,
    running: true,
    queries_24h: 4200,
    blocked_24h: 380,
    block_pct: 9.05,
    cache: 512,
    upstream_ms_avg: 12.4,
    total_blocked_domains: 158000,
    family_filter: false,
    uptime_s: 86400,
    ts: "2026-09-09T18:00:00-04:00",
    clients_using: 4,
    devices_total: 26,
    coverage_pct: 15,
  },
  top: [
    { name: "my.guarder.io", count: 300 },
    { name: "api.example.com", count: 120 },
  ],
  blocked_top: [{ name: "ads.example.com", count: 40 }],
  clients: [
    { mac: "aa:bb:cc:dd:ee:ff", ip: "192.168.1.159", name: "Windows PC", queries: 900, blocked: 30, network: null },
  ],
  rules: {
    allow: ["trusted.example.com"],
    block: ["known-bad.example.com"],
  },
  lists: [
    { name: "stevenblack", category: "ads", entries: 85000, blocked_24h: 210, enabled: true },
    { name: "oisd", category: "ads+tracking", entries: 65000, blocked_24h: 90, enabled: false },
  ],
  beaconing: [{ name: "telemetry.example.com", count: 12 }],
  suspicious: [],
  wizard: {
    resolver_ip: "192.168.1.10",
    // The LAN gateway, which is what the hub actually sends -- see
    // `hub/tests/test_dns.py`, which asserts `router_hint == "192.168.1.1"`
    // and that it differs from `resolver_ip`. This fixture used to carry a
    // sentence ("Most routers: Setup -> DHCP -> DNS Server."), which no
    // version of the hub has produced; a fixture that disagrees with the
    // contract makes the UI look right against data it will never be given.
    router_hint: "192.168.1.1",
    working: false,
    clients_using: 0,
    checked_at: null,
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
  dns?: DnsPayload;
  me?: MeOut;
};

function renderPage(element: ReactElement, overrides: Overrides = {}) {
  vi.mocked(dnsPayload).mockResolvedValue(overrides.dns ?? dnsFixture);
  // Rule/list writes are admin-only (findings: DNS rule/list-toggle writes
  // must gate the same as the reboot planner and the Security tab's fix
  // buttons) — default to admin so tests that don't care about role can
  // still exercise the write path; role-specific tests override this.
  vi.mocked(me).mockResolvedValue(overrides.me ?? ADMIN_ME);

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

describe("Dns", () => {
  it("streams frames, pauses, and keeps the buffer", async () => {
    const { emit } = renderPage(<Dns />, { dns: dnsFixture });
    emit({
      type: "dns",
      ts: "2026-09-09T18:10:00-04:00",
      data: {
        ts: "2026-09-09T18:10:00-04:00",
        mac: "aa:bb",
        name: "ads.example.com",
        blocked: true,
        list: "stevenblack",
        qtype: "A",
        ms: 1,
        client: "192.168.1.159",
        device: "Windows PC",
      },
    });
    // The same domain also tops the "most blocked" list below the stream, so
    // this asserts on the stream's own row rather than the page-wide text.
    const rows = await screen.findAllByTestId("dns-row");
    expect(rows.some((row) => row.textContent?.includes("ads.example.com"))).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: /pause/i }));

    emit({
      type: "dns",
      ts: "2026-09-09T18:10:05-04:00",
      data: { name: "second.example.com", blocked: false },
    });
    expect(screen.queryByText("second.example.com")).toBeNull();
  });

  it("adds a block rule through the hub route", async () => {
    const { posted } = renderPage(<Dns />, { dns: dnsFixture });
    const input = await screen.findByLabelText(/block a domain/i);
    await waitFor(() => expect(input).toBeEnabled());
    await userEvent.type(input, "tracker.example.com{Enter}");
    expect(posted("/api/v1/dns/rules")).toEqual({ kind: "block", domain: "tracker.example.com", op: "add" });
  });

  it("shows the wizard as working when the hub says so", async () => {
    renderPage(<Dns />, { dns: { ...dnsFixture, wizard: { ...dnsFixture.wizard, working: true, clients_using: 3 } } });
    await waitFor(() => expect(screen.getByTestId("wizard-check")).toHaveTextContent(/working/i));
  });

  it("disables rule writes for a viewer", async () => {
    renderPage(<Dns />, { dns: dnsFixture, me: viewerMe });
    expect(await screen.findByLabelText(/block a domain/i)).toBeDisabled();
  });

  it("disables rule writes for an operator, with an admin-role tooltip", async () => {
    renderPage(<Dns />, { dns: dnsFixture, me: OPERATOR_ME });
    const input = await screen.findByLabelText(/block a domain/i);
    expect(input).toBeDisabled();
    expect(input).toHaveAttribute("title", "admin role required");
  });

  it("enables rule writes for an admin", async () => {
    renderPage(<Dns />, { dns: dnsFixture, me: ADMIN_ME });
    const input = await screen.findByLabelText(/block a domain/i);
    await waitFor(() => expect(input).toBeEnabled());
  });

  it("subscribes to the dns live frame type through the live feed's subscribe API", async () => {
    renderPage(<Dns />, { dns: dnsFixture });
    // Anchored on the card heading: the wizard's own help text names DNS
    // several times ("DNS Server", "Custom DNS", "Secure DNS"), so a bare
    // text query is ambiguous.
    await screen.findByRole("heading", { name: "DNS" });
    expect(useLiveFeedMock).toHaveBeenCalledWith(expect.objectContaining({ subscribe: ["dns"] }));
  });
});

describe("what the DNS numbers describe", () => {
  // A block rate computed from the resolver's logs says nothing about a
  // device that never asks it — and that is the device you would most want
  // to know about. Without the ratio on the page, "3.2% blocked" reads as a
  // fact about the network rather than about four of its devices.
  it("says how many of the network's devices the figures cover", async () => {
    renderPage(<Dns />, { dns: dnsFixture });
    expect(await screen.findByTestId("dns-coverage")).toHaveTextContent(
      "Based on 4 of 26 devices (15% coverage)",
    );
  });

  it("warns that encrypted DNS is invisible here", async () => {
    renderPage(<Dns />, { dns: dnsFixture });
    expect(await screen.findByTestId("dns-coverage")).toHaveTextContent(
      /encrypted DNS, do not appear here/,
    );
  });
});
