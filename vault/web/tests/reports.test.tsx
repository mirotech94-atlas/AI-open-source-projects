import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import Reports from "../src/pages/Reports";
import type { MeOut } from "../src/api";
import type { ReportCard, ReportsPayload } from "../src/types";

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
    reportsPayload: vi.fn(),
    reportPreview: vi.fn(() => Promise.resolve("<html></html>")),
    buildReport: vi.fn((days: number, preset: string, sections: string[], title: string) => {
      postedCalls.push({ path: "/api/v1/reports/build", body: { days, preset, sections, title } });
      return Promise.resolve({
        id: "new-1",
        kind: "adhoc",
        period_days: days,
        preset,
        title,
        created: "2026-09-10T12:00:00-04:00",
        score: 91,
        url: "/api/v1/reports/new-1",
        share_url: null,
        share_expires: null,
      } as ReportCard);
    }),
    shareReport: vi.fn((id: string, hours: number, push = false) => {
      postedCalls.push({ path: `/api/v1/reports/${id}/share`, body: { hours, push } });
      return Promise.resolve({ share_url: "https://my.guarder.io/r/abc123", pushed: push });
    }),
    deleteReport: vi.fn(() => Promise.resolve({})),
    scheduleReports: vi.fn((body: unknown) => {
      postedCalls.push({ path: "/api/v1/reports/schedule", body });
      return Promise.resolve({});
    }),
  };
});

import { reportPreview, reportsPayload, shareReport } from "../src/lib/api";

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    me: vi.fn(() =>
      Promise.resolve({
        user: { username: "myron", display_name: "Myron", is_superadmin: false },
        tenants: [{ id: "t1", slug: "t1", name: "T1", role: "admin" }],
        active_tenant: { id: "t1", slug: "t1", name: "T1", role: "admin" },
        chat_providers: [],
      }),
    ),
  };
});

import { me } from "../src/api";

afterEach(() => {
  postedCalls.length = 0;
  vi.clearAllMocks();
  vi.useRealTimers();
});

// --- fixtures ----------------------------------------------------------------

export const reportsFixture: ReportsPayload = {
  library: [
    {
      id: "r1",
      kind: "weekly",
      period_days: 7,
      preset: "full",
      title: "Week 36",
      created: "2026-09-03T08:00:00-04:00",
      score: 87,
      url: "/api/v1/reports/r1",
      share_url: null,
      share_expires: null,
    },
    {
      id: "r2",
      kind: "adhoc",
      period_days: 30,
      preset: "security",
      title: "Security check",
      created: "2026-09-01T08:00:00-04:00",
      score: 74,
      url: "/api/v1/reports/r2",
      share_url: null,
      share_expires: null,
    },
  ],
  presets: [
    { key: "full", label: "Full report", sections: ["overview", "security", "presence", "devices"] },
    { key: "security", label: "Security", sections: ["security"] },
    { key: "executive", label: "Executive summary", sections: ["overview"] },
    { key: "devices", label: "Devices", sections: ["devices"] },
    { key: "presence", label: "Presence & screen time", sections: ["presence"] },
    { key: "network", label: "Network gear", sections: ["network"] },
    { key: "cameras", label: "Cameras", sections: ["cameras"] },
    { key: "phones", label: "Phones, tablets & computers", sections: ["phones"] },
    { key: "tvs", label: "TVs & streaming", sections: ["tvs"] },
    { key: "smarthome", label: "Smart home", sections: ["smarthome"] },
    { key: "networkgear", label: "Network gear (deep)", sections: ["network", "security"] },
  ],
  sections: [
    { key: "overview", title: "Overview" },
    { key: "security", title: "Servers & security posture" },
    { key: "presence", title: "Presence & screen time" },
    { key: "devices", title: "Devices" },
    { key: "network", title: "Network gear" },
    { key: "cameras", title: "Cameras" },
    { key: "phones", title: "Phones, tablets & computers" },
    { key: "tvs", title: "TVs & streaming" },
    { key: "smarthome", title: "Smart home" },
  ],
  schedule: { weekly: true, monthly: false, preset: "full", ntfy: true, hour: 8 },
      value: { days: 30, stats: [], work: [] },
};

const ADMIN_ME: MeOut = {
  user: { username: "a", display_name: "Admin", is_superadmin: false },
  tenants: [{ id: "t1", slug: "t1", name: "T1", role: "admin" }],
  active_tenant: { id: "t1", slug: "t1", name: "T1", role: "admin" },
  chat_providers: [],
};

type Overrides = {
  reports?: ReportsPayload;
  share?: { share_url: string; pushed?: boolean };
  me?: MeOut;
};

function renderPage(element: ReactElement, overrides: Overrides = {}) {
  vi.mocked(reportsPayload).mockResolvedValue(overrides.reports ?? reportsFixture);
  vi.mocked(me).mockResolvedValue(overrides.me ?? ADMIN_ME);
  if (overrides.share) {
    vi.mocked(shareReport).mockImplementation((id: string, hours: number, push = false) => {
      postedCalls.push({ path: `/api/v1/reports/${id}/share`, body: { hours, push } });
      return Promise.resolve(overrides.share as { share_url: string; pushed?: boolean });
    });
  }

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>{element}</ToastProvider>
    </QueryClientProvider>,
  );

  return {
    posted,
    fetchCount: (path: string) => {
      if (path === "/api/v1/reports/preview") return vi.mocked(reportPreview).mock.calls.length;
      if (path === "/api/v1/reports") return vi.mocked(reportsPayload).mock.calls.length;
      return 0;
    },
  };
}

describe("Reports", () => {
  it("pre-ticks a preset's sections and debounces the preview", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { fetchCount } = renderPage(<Reports />, { reports: reportsFixture });
    await userEvent.selectOptions(await screen.findByLabelText(/preset/i), "security");
    expect(screen.getByLabelText("Servers & security posture")).toBeChecked();
    expect(screen.getByLabelText("Presence & screen time")).not.toBeChecked();
    vi.advanceTimersByTime(500);
    expect(fetchCount("/api/v1/reports/preview")).toBe(1);
  });

  it("builds with the chosen period and title", async () => {
    const { posted } = renderPage(<Reports />, { reports: reportsFixture });
    await userEvent.type(await screen.findByLabelText(/title/i), "Week 37");
    await userEvent.selectOptions(screen.getByLabelText(/period/i), "30");
    await userEvent.click(screen.getByRole("button", { name: /build report/i }));
    expect(posted("/api/v1/reports/build")).toMatchObject({ days: 30, title: "Week 37" });
  });

  it("shows the share URL and warns that it is public", async () => {
    renderPage(<Reports />, {
      reports: reportsFixture,
      share: { share_url: "https://my.guarder.io/r/abc123" },
    });
    await userEvent.click((await screen.findAllByRole("button", { name: /share/i }))[0]);
    await userEvent.click(screen.getByRole("button", { name: /create link/i }));
    expect(await screen.findByDisplayValue(/\/r\/abc123$/)).toBeInTheDocument();
    expect(screen.getByText(/anyone with the link/i)).toBeInTheDocument();
  });

  it("sends the freshly minted link to the phone only when asked", async () => {
    const { posted } = renderPage(<Reports />, {
      reports: reportsFixture,
      share: { share_url: "https://my.guarder.io/r/abc123", pushed: true },
    });
    await userEvent.click((await screen.findAllByRole("button", { name: /share/i }))[0]);

    // Creating a link must not notify anyone.
    await userEvent.click(screen.getByRole("button", { name: /create link/i }));
    expect(posted(`/api/v1/reports/${reportsFixture.library[0].id}/share`)).toMatchObject({ push: false });
    expect(screen.queryByText(/queued for delivery to your notification channels/i)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /send to my phone/i }));
    expect(posted(`/api/v1/reports/${reportsFixture.library[0].id}/share`)).toMatchObject({ push: true });
    expect(await screen.findByText(/queued for delivery to your notification channels/i)).toBeInTheDocument();
  });

  it("pre-ticks the default preset once a delayed reports payload resolves", async () => {
    let resolvePayload!: (value: ReportsPayload) => void;
    vi.mocked(reportsPayload).mockImplementation(
      () => new Promise<ReportsPayload>((resolve) => (resolvePayload = resolve)),
    );
    vi.mocked(me).mockResolvedValue(ADMIN_ME);

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <Reports />
        </ToastProvider>
      </QueryClientProvider>,
    );

    // Before the query resolves, the builder renders against the empty
    // placeholder payload — no presets, so nothing to pre-tick yet.
    expect(screen.queryByLabelText("Overview")).not.toBeInTheDocument();

    resolvePayload(reportsFixture);

    // Once the real payload lands, the default "full" preset and its
    // sections should be selected — this is the seeding effect, not the
    // (now-empty) useState initializer.
    await screen.findByLabelText("Overview");
    expect(screen.getByLabelText(/preset/i)).toHaveValue("full");
    expect(screen.getByLabelText("Overview")).toBeChecked();
    expect(screen.getByLabelText("Servers & security posture")).toBeChecked();
    expect(screen.getByLabelText("Devices")).toBeChecked();

    await userEvent.click(screen.getByRole("button", { name: /build report/i }));
    expect(posted("/api/v1/reports/build")).toMatchObject({ preset: "full" });
  });

  it("shows the tenant's real schedule once a delayed reports payload resolves", async () => {
    let resolvePayload!: (value: ReportsPayload) => void;
    vi.mocked(reportsPayload).mockImplementation(
      () => new Promise<ReportsPayload>((resolve) => (resolvePayload = resolve)),
    );
    vi.mocked(me).mockResolvedValue(ADMIN_ME);

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <Reports />
        </ToastProvider>
      </QueryClientProvider>,
    );

    // The placeholder schedule is all-false; the real fixture has
    // `weekly: true` — confirm the placeholder isn't what gets shown once
    // data actually arrives, and isn't what gets saved if left untouched.
    resolvePayload(reportsFixture);

    // The weekly/monthly checkboxes are rendered unconditionally from the
    // very first paint (unlike the builder's section checkboxes, which
    // only exist once `sections` is populated), so `findByLabelText` alone
    // would resolve immediately against the pre-load DOM node. Wait for
    // its checked state to actually flip once the real schedule lands.
    await waitFor(() => expect(screen.getByLabelText(/weekly, every monday/i)).toBeChecked());
    expect(screen.getByLabelText(/monthly, on the 1st/i)).not.toBeChecked();

    await userEvent.click(screen.getByRole("button", { name: /save schedule/i }));
    expect(posted("/api/v1/reports/schedule")).toMatchObject({ weekly: true, monthly: false });
  });

  it("keeps the newest preview response even when an older request resolves later", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    const deferred: Array<{ resolve: (html: string) => void }> = [];
    vi.mocked(reportPreview).mockImplementation(
      () =>
        new Promise<string>((resolve) => {
          deferred.push({ resolve });
        }),
    );

    renderPage(<Reports />, { reports: reportsFixture });

    // First edit — fires the first debounced preview request.
    await userEvent.selectOptions(await screen.findByLabelText(/preset/i), "security");
    vi.advanceTimersByTime(500);
    expect(deferred).toHaveLength(1);

    // Second edit before the first request resolves — fires a second,
    // newer request.
    await userEvent.selectOptions(screen.getByLabelText(/preset/i), "devices");
    vi.advanceTimersByTime(500);
    expect(deferred).toHaveLength(2);

    // Resolve out of order: the newer (second) request settles first,
    // then the stale (first) request settles after it.
    await act(async () => {
      deferred[1].resolve("<p>devices preview</p>");
      await Promise.resolve();
    });
    await act(async () => {
      deferred[0].resolve("<p>security preview (stale)</p>");
      await Promise.resolve();
    });

    await waitFor(() => {
      const frame = document.querySelector("iframe.g-reports-preview-frame");
      expect(frame?.getAttribute("srcdoc")).toBe("<p>devices preview</p>");
    });
  });
});
