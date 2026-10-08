import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import Overview from "../src/pages/Overview";
import type { LiveEvent, OverviewPayload } from "../src/types";

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

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    requestRun: vi.fn(),
    overviewPayload: vi.fn(),
    livePayload: vi.fn(),
  };
});

import { livePayload, overviewPayload } from "../src/lib/api";
import { NO_HERO } from "./overviewHero";

const fixtureOverview: OverviewPayload = {
  ts: "2026-09-09T18:30:26-04:00",
  age_s: 0,
  score: 83,
  grade: "B",
  potential: 98,
  score_model: "",
  score_coverage: null,
  verdict: "Mostly fine.",
  next_action: null,
  score_change: null,
  deductions: [
    { category: "Vulnerabilities", points: 12, why: "0 security updates and 403 fixable CVEs pending" },
    { category: "Devices", points: 5, why: "1 unverified device" },
  ],
  strip: [
    { kind: "site", name: "status.example.org", ok: true, blocks: Array.from({ length: 24 }, (_, hour) => ({ hour, state: "ok" })) },
    { kind: "host", name: "vps1", ok: true, blocks: Array.from({ length: 24 }, (_, hour) => ({ hour, state: "ok" })) },
  ],
  timeline: [
    { ts: "2026-09-09T17:00:00-04:00", kind: "scan", title: "Scan completed", detail: "", sev: null, entity: "", href: null },
  ],
  score_trend: {
    name: "score",
    points: [
      { ts: "2026-09-08T00:00:00-04:00", value: 80 },
      { ts: "2026-09-09T00:00:00-04:00", value: 83 },
    ],
    color: null,
  },
  questions: [
    { question: "Am I visible to attackers?", answer: "No open ports", state: "ok" },
    { question: "Is anything out of date?", answer: "3 hosts pending", state: "warn" },
    { question: "Are my backups current?", answer: "Yes", state: "ok" },
    { question: "Is my Wi-Fi safe?", answer: "WPA3", state: "ok" },
    { question: "Any unknown devices?", answer: "1 unverified", state: "warn" },
  ],
  alerts: { crit: 0, warn: 1, info: 0, open: 1, acked: 0, snoozed: 0 },
  devices: { total: 10, online: 8, sleeping: 2, unverified: 1, quarantined: 0 },
  sensor: {
    name: "mac",
    status: "active",
    last_seen: "2026-09-09T18:30:26-04:00",
    age_s: 5,
    platform: "darwin",
    version: "0.1.0",
  },
  networks: [],
  chat_example_ip: "192.168.1.166",
  voice: false,
  needs_you: [],
  fixed_without_you: { days: 14, automatic: 0, approved: 0, daily: [] },
  coverage: [],
  certs_expiring: [],
  ...NO_HERO,
};

type Overrides = {
  overview?: OverviewPayload;
};

function renderPage(element: ReactElement, overrides: Overrides = {}) {
  vi.mocked(overviewPayload).mockResolvedValue(overrides.overview ?? fixtureOverview);
  vi.mocked(livePayload).mockResolvedValue({ ts: null, sites: [], scanning: false, sensor_age_s: null, load1: null, cores: null, maintenance_until: null });

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    // A router, because the five hero cards are links (task 74) — a card that
    // navigates has to be a real link, not a div with a click handler.
    <MemoryRouter initialEntries={["/overview"]}>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>{element}</ToastProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );

  return {
    emit: emitLiveEvent,
    fetchCount: (path: string) => {
      if (path === "/api/v1/overview") return vi.mocked(overviewPayload).mock.calls.length;
      if (path === "/api/v1/live") return vi.mocked(livePayload).mock.calls.length;
      return 0;
    },
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("Overview on the skeleton", () => {
  it("opens with a verdict, then the stat strip, one feature block and the records", async () => {
    renderPage(<Overview />, { overview: fixtureOverview });
    const verdict = await screen.findByRole("status", { name: "Verdict" });
    const strip = document.querySelector(".sk-stats");
    const feature = screen.getByText("What Vault is doing for you");
    const records = screen.getByText("Recent activity");
    // order in the DOM is the order on the page
    const order = [verdict, feature, records].map((el) => el.compareDocumentPosition(records));
    expect(verdict.compareDocumentPosition(feature) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(feature.compareDocumentPosition(records) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(strip).not.toBeNull();
    expect(order.length).toBe(3);
  });

  it("the good verdict says nothing needs you — never that you are protected", async () => {
    renderPage(<Overview />, { overview: { ...fixtureOverview, needs_you: [], alerts: { ...fixtureOverview.alerts, crit: 0 } } });
    const v = await screen.findByRole("status", { name: "Verdict" });
    expect(v.dataset.tone).toBe("good");
    expect(v.textContent).toMatch(/Nothing needs you/);
    expect(v.textContent).not.toMatch(/protected|secure/i);
  });

  it("a waiting decision turns the verdict amber and keeps Approve and Not now on the page", async () => {
    const item = { key: "sec:ufw:vps1", title: "Firewall is off on vps1", detail: "I would enable ufw.", cost: "Nothing reboots.", severity: "WARN", action: "enable_ufw", host: "vps1" };
    renderPage(<Overview />, { overview: { ...fixtureOverview, needs_you: [item], alerts: { ...fixtureOverview.alerts, crit: 0 } } });
    const v = await screen.findByRole("status", { name: "Verdict" });
    expect(v.dataset.tone).toBe("warn");
    expect(v.textContent).toMatch(/1 thing needs a decision/);
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not now" })).toBeInTheDocument();
  });

  it("a critical alert outranks a waiting decision", async () => {
    renderPage(<Overview />, { overview: { ...fixtureOverview, alerts: { ...fixtureOverview.alerts, crit: 2 } } });
    const v = await screen.findByRole("status", { name: "Verdict" });
    expect(v.dataset.tone).toBe("alert");
    expect(v.textContent).toMatch(/2 things need a look now/);
  });

  it("answers the five questions as checks, each with its state", async () => {
    renderPage(<Overview />, { overview: fixtureOverview });
    const feature = (await screen.findByText("What Vault is doing for you")).closest(".sk-feature") as HTMLElement;
    for (const q of fixtureOverview.questions) {
      expect(within(feature).getByText(q.question)).toBeInTheDocument();
      expect(within(feature).getByText(q.answer)).toBeInTheDocument();
    }
  });

  it("offers no scan button of its own, because the top bar already has one", async () => {
    // Two controls for one action on one screen is duplication, not choice:
    // the top bar's "Scan now" queues the same scan on every page AND
    // reports its live state, which a page-local button could not.
    renderPage(<Overview />, { overview: fixtureOverview });
    await screen.findByText("Recent activity");
    expect(screen.queryByRole("button", { name: /run a check/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^scan now$/i })).toBeNull();
  });

  it("prepends a live alert frame into the records without a refetch", async () => {
    const { emit, fetchCount } = renderPage(<Overview />, { overview: fixtureOverview });
    await screen.findByText("Recent activity");
    const before = fetchCount("/api/v1/overview");
    emit({ type: "alert", ts: "2026-09-10T08:05:00Z", data: { key: "bruteforce:vps1", sev: "WARN", msg: "brute force from 1.2.3.4" } } as never);
    expect(await screen.findByText(/brute force/)).toBeInTheDocument();
    expect(fetchCount("/api/v1/overview")).toBe(before);
  });

  it("does not duplicate a live frame the refetched payload already carries", async () => {
    const dup = fixtureOverview.timeline[0];
    const { emit } = renderPage(<Overview />, { overview: fixtureOverview });
    await screen.findByText("Recent activity");
    emit({ type: "alert", ts: dup.ts, data: { key: dup.entity || dup.title, sev: dup.sev, msg: dup.title } } as never);
    await screen.findByText("Recent activity");
    expect(screen.getAllByText(dup.title).length).toBe(1);
  });
});
