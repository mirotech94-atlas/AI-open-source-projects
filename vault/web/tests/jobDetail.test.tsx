import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import type { JobRow } from "../src/types";

vi.mock("../src/lib/api", () => ({
  get: () => Promise.resolve({ id: "s1", ts: "2026-09-27T17:30:00-04:00", score: 92, devices: 37,
    sites_up: 3, sites_total: 3, hosts_reachable: 2, hosts_total: 3, alerts: 4 }),
}));

import { JobDetail } from "../src/components/JobDetail";

const base: JobRow = {
  id: "q1", kind: "scan", label: "network scan", target: "", who: "miro", status: "done",
  queued_at: "2026-09-27T21:29:55+00:00", finished_at: "2026-09-27T21:30:00+00:00",
  sensor_id: null, started_at: null, not_before: null, expires_at: null, wait_reason: "",
  waiting_s: 0, running_s: null, duration_s: 5, ok: true, out: "scan requested",
  why_failed: "", stuck: false, retryable: false,
} as JobRow;

function renderDetail(row: JobRow) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><JobDetail row={row} /></QueryClientProvider>);
}

describe("Activity drawer", () => {
  it("opens the scan that followed a scan request, and offers it as a download", async () => {
    renderDetail({ ...base, scan_id: "s1" });
    expect(await screen.findByText("The scan that followed this request")).toBeInTheDocument();
    expect(screen.getByText("37")).toBeInTheDocument();
    const dl = screen.getByRole("link", { name: /download the full scan/i });
    expect(dl).toHaveAttribute("href", "/api/v1/scans/s1/download");
  });

  it("says so when no scan has arrived since the request", () => {
    renderDetail({ ...base, scan_id: null });
    expect(screen.getByText(/has not sent a scan since/i)).toBeInTheDocument();
  });

  it("opens a report and its PDF", () => {
    renderDetail({ ...base, id: "report:r9", kind: "report", label: "Weekly report", report_id: "r9" });
    expect(screen.getByRole("link", { name: "Open the report" })).toHaveAttribute("href", "/api/v1/reports/r9");
    expect(screen.getByRole("link", { name: "Download PDF" })).toHaveAttribute("href", "/api/v1/reports/r9/pdf");
  });
});
