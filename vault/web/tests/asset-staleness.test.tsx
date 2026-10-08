// A site or host the sensor has stopped reporting must stop presenting its
// last result as a current one. The card is the surface that made the old
// behaviour convincing: a frozen row wore a green "up" badge and a hardening
// grade that looked exactly like a live reading.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HostCardView } from "../src/components/HostCardView";
import { SiteCardView } from "../src/components/SiteCardView";
import type { HostCard, SiteCard } from "../src/types";

function site(over: Partial<SiteCard> = {}): SiteCard {
  return {
    name: "safehome",
    url: "https://safehome.example",
    ok: true,
    code: 200,
    ms: 120,
    uptime_24h: 100,
    uptime_7d: 100,
    uptime_24h_checks: 288,
    uptime_7d_checks: 2016,
    uptime_24h_planned: 0,
    uptime_7d_planned: 0,
    latency_spark: [1, 2, 3],
    cert_days: 300,
    cert_expires: "2027-01-01",
    cert_issuer: "R3",
    dns_a: ["10.0.0.1"],
    headers: {
      hsts: true, nosniff: true, frame: true, csp: false,
      https_redirect: true, old_tls: false, server: "nginx",
    },
    incidents_7d: 0,
    public: true,
    stale: false,
    stale_note: "",
    last_checked: "",
    ...over,
  };
}

function host(over: Partial<HostCard> = {}): HostCard {
  return {
    name: "records",
    address: "10.0.0.9",
    reachable: true,
    hostname: "records",
    uptime: "9 days",
    load1: 0.2,
    load_per_core: 0.1,
    disk_pct: 40,
    mem_pct: 50,
    failed_ssh_24h: 0,
    banned: 0,
    fail2ban: true,
    reboot_required: false,
    pending_updates: 0,
    security_updates: 0,
    cve_fixable: 0,
    hardening: { grade: "A", score: 95, checks: [] } as HostCard["hardening"],
    ports: [],
    docker: [],
    services: [],
    gauges: [],
    stale: false,
    stale_note: "",
    last_checked: "",
    ...over,
  };
}

describe("a site the sensor still reports", () => {
  it("shows its real state and says nothing about staleness", () => {
    render(<SiteCardView site={site()} certWarnDays={21} onOpen={() => {}} />);
    expect(screen.getByText("up")).toBeInTheDocument();
    expect(screen.queryByTestId("site-stale-note")).not.toBeInTheDocument();
    expect(screen.getByTestId("site-card").className).not.toContain("g-asset-stale");
  });
});

describe("a site the sensor has stopped reporting", () => {
  const stale = site({
    stale: true,
    stale_note: "The sensor has stopped reporting this — last checked 12 Sep 10:00 UTC. The figures shown are from then, not now.",
    last_checked: "2026-09-12T10:00:00+00:00",
  });

  it('stops claiming "up", because that verdict is exactly as old as the rest', () => {
    render(<SiteCardView site={stale} certWarnDays={21} onOpen={() => {}} />);
    expect(screen.queryByText("up")).not.toBeInTheDocument();
    expect(screen.getByText("not checked")).toBeInTheDocument();
  });

  it("says when it was actually last checked", () => {
    render(<SiteCardView site={stale} certWarnDays={21} onOpen={() => {}} />);
    expect(screen.getByTestId("site-stale-note")).toHaveTextContent("last checked 12 Sep 10:00 UTC");
  });

  it("drops the confident tone rather than only adding a note", () => {
    render(<SiteCardView site={stale} certWarnDays={21} onOpen={() => {}} />);
    const card = screen.getByTestId("site-card");
    expect(card.className).toContain("g-asset-stale");
    expect(card.className).toContain("g-tone-plain");
    expect(card.className).not.toContain("g-tone-good");
  });

  it("still opens, so its history stays reachable", async () => {
    let opened = 0;
    render(<SiteCardView site={stale} certWarnDays={21} onOpen={() => { opened += 1; }} />);
    screen.getByTestId("site-card").click();
    expect(opened).toBe(1);
  });

  it("does not paint a down site green just because it is stale", () => {
    render(
      <SiteCardView site={{ ...stale, ok: false }} certWarnDays={21} onOpen={() => {}} />,
    );
    const card = screen.getByTestId("site-card");
    expect(card.className).not.toContain("g-tone-good");
    expect(screen.getByText("not checked")).toBeInTheDocument();
  });
});

describe("a host the sensor has stopped reporting", () => {
  const stale = host({
    stale: true,
    stale_note: "The sensor has stopped reporting this — last checked 12 Sep 10:00 UTC. The figures shown are from then, not now.",
  });

  it("dims the card and explains why", () => {
    render(<HostCardView host={stale} onOpen={() => {}} />);
    expect(screen.getByTestId("host-stale-note")).toHaveTextContent("stopped reporting");
    expect(screen.getByTestId("host-card").className).toContain("g-asset-stale");
  });

  it("stops presenting a months-old hardening grade as a current one", () => {
    render(<HostCardView host={stale} onOpen={() => {}} />);
    const card = screen.getByTestId("host-card");
    expect(card.className).toContain("g-tone-plain");
    expect(card.className).not.toContain("g-tone-good");
  });

  it("says nothing about staleness while it is still being reported", () => {
    render(<HostCardView host={host()} onOpen={() => {}} />);
    expect(screen.queryByTestId("host-stale-note")).not.toBeInTheDocument();
  });
});
