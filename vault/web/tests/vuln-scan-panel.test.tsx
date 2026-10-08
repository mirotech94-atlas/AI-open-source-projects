// Security → Vulnerability scans: pick a target, run, read the report.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import type { VulnScansPayload } from "../src/types";

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, vulnScans: vi.fn(), startVulnScan: vi.fn() };
});
import { startVulnScan, vulnScans } from "../src/lib/api";
import { VulnScanPanel } from "../src/components/VulnScanPanel";

const PAYLOAD: VulnScansPayload = {
  targets: [
    { kind: "lan", target: "lan", label: "Whole Wi-Fi / LAN" },
    { kind: "host", target: "prod", label: "prod · server" },
    { kind: "device", target: "192.168.1.9", label: "Camera · 192.168.1.9" },
  ],
  scans: [
    {
      id: "q1", status: "done", kind: "device", target: "192.168.1.9", profile: "quick", who: "miro",
      queued_at: "2026-09-14T18:00:00Z", finished_at: "2026-09-14T18:00:04Z", duration_s: 3.6, score: 86,
      summary: { critical: 0, high: 1, medium: 0, low: 1, info: 0 },
      ports: [{ port: 23, service: "telnet", banner: "", http: null, tls_version: "", tls_verified: null }],
      findings: [
        { severity: "high", title: "telnet open on port 23", evidence: "telnet is unencrypted", port: 23, fix: null, cve: null, target: "" },
        { severity: "low", title: "Certificate is self-signed or not trusted", evidence: "", port: 443, fix: null, cve: null, target: "" },
      ],
      error: "",
    },
    { id: "q2", status: "failed", kind: "host", target: "prod", profile: "quick", who: "miro", queued_at: "2026-09-14T17:00:00Z",
      finished_at: null, duration_s: null, score: null, summary: {}, ports: [], findings: [], error: "ssh failed" },
  ],
};

function mount(isAdmin = true, canWrite = true) {
  (vulnScans as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(PAYLOAD);
  (startVulnScan as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ status: "queued", id: "q3" });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ToastProvider>
        <VulnScanPanel canWrite={canWrite} isAdmin={isAdmin} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("Vulnerability scans", () => {
  it("lists past scans with their score and a severity summary, and failures with their error", async () => {
    mount();
    const rows = await screen.findAllByTestId("vuln-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("192.168.1.9");
    expect(rows[0]).toHaveTextContent("86");
    expect(rows[0]).toHaveTextContent("1 high · 1 low");
    expect(rows[1]).toHaveTextContent("ssh failed");
  });

  it("opens a report with every finding graded and the open ports named", async () => {
    mount();
    await userEvent.click((await screen.findAllByTestId("vuln-row"))[0]);
    const detail = screen.getByTestId("vuln-detail");
    expect(detail).toHaveTextContent("23/telnet");
    expect(within(detail).getByText("telnet open on port 23")).toBeInTheDocument();
    expect(within(detail).getAllByText("high").length).toBeGreaterThan(0);
  });

  it("runs a scan against the chosen target at the chosen depth", async () => {
    mount();
    await screen.findAllByTestId("vuln-row");
    await userEvent.selectOptions(screen.getByLabelText("Target"), "host:prod");
    await userEvent.selectOptions(screen.getByLabelText("Depth"), "full");
    await userEvent.click(screen.getByRole("button", { name: /run scan/i }));
    expect(startVulnScan).toHaveBeenCalledWith("prod", "host", "full");
  });

  it("hides the whole-network target from a non-admin", async () => {
    mount(false);
    await screen.findAllByTestId("vuln-row");
    const options = within(screen.getByLabelText("Target")).getAllByRole("option").map((o) => o.textContent);
    expect(options.some((t) => t?.includes("Whole Wi-Fi"))).toBe(false);
  });
});
