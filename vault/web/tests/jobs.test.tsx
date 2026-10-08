import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import Jobs from "../src/pages/Jobs";
import type { MeOut } from "../src/api";
import type { JobRow, JobsPayload } from "../src/types";

const { postedCalls } = vi.hoisted(() => {
  return { postedCalls: [] as { path: string }[] };
});

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    jobsPayload: vi.fn(),
    jobRetry: vi.fn((id: string) => {
      postedCalls.push({ path: `/api/v1/jobs/${id}/retry` });
      return Promise.resolve({ id: "q-retry-1", status: "queued" });
    }),
  };
});

import { jobsPayload } from "../src/lib/api";

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    me: vi.fn(),
  };
});

import { me } from "../src/api";

afterEach(() => {
  postedCalls.length = 0;
  vi.clearAllMocks();
});

function row(overrides: Partial<JobRow>): JobRow {
  return {
    id: "q1",
    kind: "speedtest",
    label: "speed test",
    target: "",
    who: "miro",
    status: "done",
    queued_at: "2026-09-10T10:00:00+00:00",
    finished_at: "2026-09-10T10:00:05+00:00",
    sensor_id: null, started_at: null, not_before: null, expires_at: null,
    wait_reason: "", waiting_s: 0, running_s: null,
    duration_s: 5,
    ok: true,
    out: "",
    why_failed: "",
    stuck: false,
    retryable: false,
    ...overrides,
  };
}

const OPERATOR_ME: MeOut = {
  user: { username: "op", display_name: "Op", is_superadmin: false },
  tenants: [{ id: "t1", slug: "t1", name: "T1", role: "operator" }],
  active_tenant: { id: "t1", slug: "t1", name: "T1", role: "operator" },
  chat_providers: [],
};

const VIEWER_ME: MeOut = {
  user: { username: "v", display_name: "V", is_superadmin: false },
  tenants: [{ id: "t1", slug: "t1", name: "T1", role: "viewer" }],
  active_tenant: { id: "t1", slug: "t1", name: "T1", role: "viewer" },
  chat_providers: [],
};

type Overrides = { jobs?: JobRow[]; me?: MeOut };

function renderPage(overrides: Overrides = {}) {
  vi.mocked(jobsPayload).mockResolvedValue({ jobs: overrides.jobs ?? [] } as JobsPayload);
  vi.mocked(me).mockResolvedValue(overrides.me ?? OPERATOR_ME);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <Jobs />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("Jobs / Activity page", () => {
  it("shows the plain-English label, never the raw path", async () => {
    renderPage({ jobs: [row({ label: "free disk space", target: "prod", kind: "fix" })] });
    expect(await screen.findByText("free disk space")).toBeInTheDocument();
    expect(screen.queryByText(/\/api\//)).not.toBeInTheDocument();
  });

  it("separates queued/running jobs from finished ones", async () => {
    renderPage({
      jobs: [
        row({ id: "a", status: "queued", label: "speed test" }),
        row({ id: "b", status: "done", label: "network scan", kind: "scan" }),
      ],
    });
    await screen.findByText("speed test");
    const inProgress = screen.getByText("In progress").closest("section")!;
    const history = screen.getByText("History").closest("section")!;
    expect(inProgress.textContent).toContain("speed test");
    expect(history.textContent).toContain("network scan");
    expect(inProgress.textContent).not.toContain("network scan");
  });

  it("calls a stuck running job out instead of showing it as ordinary progress", async () => {
    renderPage({ jobs: [row({ status: "running", stuck: true, label: "speed test", duration_s: 2700 })] });
    expect(await screen.findByText("Stuck")).toBeInTheDocument();
  });

  it("shows why a failed job failed, in first person", async () => {
    renderPage({
      jobs: [
        row({
          status: "failed",
          ok: false,
          label: "free disk space",
          target: "prod",
          why_failed: "I tried to run free disk space on prod, but it failed: no space left on device.",
        }),
      ],
    });
    expect(await screen.findByText(/^I tried to run/)).toBeInTheDocument();
  });

  it("offers Retry only for a failed, retryable job", async () => {
    renderPage({
      jobs: [
        row({ id: "r1", status: "failed", retryable: true, label: "speed test" }),
        row({ id: "r2", status: "failed", retryable: false, label: "full apt upgrade (all packages)" }),
        row({ id: "r3", status: "done", retryable: false, label: "network scan" }),
      ],
    });
    await screen.findByText("speed test");
    expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(1);
  });

  it("lets an operator retry a failed job", async () => {
    renderPage({ jobs: [row({ id: "r1", status: "failed", retryable: true, label: "speed test" })] });
    const button = await screen.findByRole("button", { name: "Retry" });
    expect(button).not.toBeDisabled();
    await userEvent.click(button);
    await waitFor(() => expect(postedCalls).toContainEqual({ path: "/api/v1/jobs/r1/retry" }));
  });

  it("renders Retry disabled with a role tooltip for a viewer", async () => {
    renderPage({ jobs: [row({ id: "r1", status: "failed", retryable: true, label: "speed test" })], me: VIEWER_ME });
    const button = await screen.findByRole("button", { name: "Retry" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "operator role required");
    await userEvent.click(button);
    expect(postedCalls).toHaveLength(0);
  });

  it("filters by status", async () => {
    renderPage({
      jobs: [
        row({ id: "a", status: "queued", label: "speed test" }),
        row({ id: "b", status: "failed", label: "network scan", kind: "scan" }),
      ],
    });
    await screen.findByText("speed test");
    await userEvent.click(screen.getByRole("button", { name: "Queued" }));
    expect(screen.queryByText("network scan")).not.toBeInTheDocument();
    expect(screen.getByText("speed test")).toBeInTheDocument();
  });

  it("filters by kind", async () => {
    renderPage({
      jobs: [
        row({ id: "a", status: "done", label: "speed test", kind: "speedtest" }),
        row({ id: "b", status: "done", label: "network scan", kind: "scan" }),
      ],
    });
    await screen.findByText("speed test");
    await userEvent.click(screen.getByRole("button", { name: "Speed test" }));
    expect(screen.queryByText("network scan")).not.toBeInTheDocument();
    expect(screen.getByText("speed test")).toBeInTheDocument();
  });

  it("Pause feed stops the poll and flips to Resume feed", async () => {
    renderPage({ jobs: [row({ id: "a", status: "queued", label: "speed test" })] });
    await screen.findByText("speed test");
    const button = screen.getByRole("button", { name: /^pause feed$/i });
    await userEvent.click(button);
    expect(screen.getByRole("button", { name: /^resume feed$/i })).toBeInTheDocument();
    expect(screen.getByText(/feed paused/i)).toBeInTheDocument();
  });

  it("shows a stat strip counting running, queued, done and failed jobs", async () => {
    renderPage({
      jobs: [
        row({ id: "a", status: "queued", label: "speed test" }),
        row({ id: "b", status: "running", label: "network scan" }),
        row({ id: "c", status: "failed", label: "fix" }),
      ],
    });
    await screen.findByText("speed test");
    const strip = document.querySelector(".sk-stats");
    expect(strip).not.toBeNull();
    expect(strip).toHaveTextContent("Running");
    expect(strip).toHaveTextContent("Failed");
  });
});
