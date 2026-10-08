import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ProtectionPanel from "../src/components/ProtectionPanel";
import type { ProtectionPanel as Data } from "../src/types";

// The panel people will read to answer "am I protected from viruses and the
// dark web". The danger is not an ugly chart, it is a reassuring one: this
// must never imply Vault scans phones, and must never let "we could not
// check" look like "you are fine".

function data(over: Partial<Data> = {}): Data {
  return {
    defences: [
      { key: "sip", label: "System Integrity Protection", state: "on", buys: "stops system tampering" },
      { key: "filevault", label: "Disk encryption", state: "on", buys: "unreadable if stolen" },
      { key: "firewall", label: "Firewall", state: "unknown", buys: "refuses inbound" },
    ],
    defences_host: "This Mac",
    blocked: [
      { category: "Malware", count: 120 },
      { category: "Dark web", count: 9 },
    ],
    blocked_total: 129,
    recent: [{ name: "abcd.onion", device: "Staffphone", category: "Dark web", ts: "2026-09-20T05:00:00Z" }],
    filtering: true,
    days: 7,
    threats: [],
    ...over,
  };
}

describe("protection panel", () => {
  it("leads with what was actually stopped", () => {
    render(<ProtectionPanel data={data()} />);
    expect(screen.getByText("129")).toBeInTheDocument();
    expect(screen.getByText(/lookups refused in 7 days/)).toBeInTheDocument();
    // Scoped to the bars: "Dark web" also appears as a badge on the recent
    // lookup below, which is a different statement about a different thing.
    const bars = document.querySelector(".g-prot-bars") as HTMLElement;
    expect(within(bars).getByText("Dark web")).toBeInTheDocument();
    expect(within(bars).getByText("Malware")).toBeInTheDocument();
    expect(within(bars).getByText("9")).toBeInTheDocument();
  });

  it("never calls a check it could not run a failure", () => {
    render(<ProtectionPanel data={data()} />);
    expect(screen.getByText(/can’t tell/)).toBeInTheDocument();
    // "1 protection is off" must NOT appear: one is unknown, none are off.
    expect(screen.queryByText(/protection is off/)).toBeNull();
    expect(screen.getByText(/Nothing here is switched off/)).toBeInTheDocument();
  });

  it("counts only the ones genuinely off", () => {
    render(<ProtectionPanel data={data({
      defences: [{ key: "filevault", label: "Disk encryption", state: "off", buys: "x" }],
    })} />);
    expect(screen.getByText(/1 protection is off/)).toBeInTheDocument();
  });

  it("says plainly that it does not scan phones", () => {
    render(<ProtectionPanel data={data()} />);
    const scope = screen.getByText(/no agent on phones/i);
    expect(scope).toBeInTheDocument();
    expect(scope.textContent).toMatch(/does not scan them for viruses/i);
  });

  it("distinguishes a quiet week from a filter that is not running", () => {
    const { unmount } = render(<ProtectionPanel data={data({ blocked: [], blocked_total: 0, recent: [] })} />);
    expect(screen.getByText(/genuinely quiet rather than switched off/)).toBeInTheDocument();
    unmount();
    render(<ProtectionPanel data={data({ blocked: [], blocked_total: 0, recent: [], filtering: false })} />);
    expect(screen.getByText(/not a clean bill of health/)).toBeInTheDocument();
  });

  it("shows the blocked lookups behind the numbers", () => {
    render(<ProtectionPanel data={data()} />);
    const recent = screen.getByText("abcd.onion").closest("li") as HTMLElement;
    expect(within(recent).getByText("Staffphone")).toBeInTheDocument();
  });

  it("says so when nothing has reached for the dark web", () => {
    render(<ProtectionPanel data={data({ blocked: [{ category: "Malware", count: 4 }], blocked_total: 4 })} />);
    expect(screen.getByText(/No device has tried to reach the dark web/)).toBeInTheDocument();
  });
});
