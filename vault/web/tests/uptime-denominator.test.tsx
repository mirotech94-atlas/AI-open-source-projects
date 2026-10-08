// An availability percentage the reader cannot check is a claim, not a
// measurement. 100% over 21 checks and 100% over 2,000 were rendering
// identically, and a site with too few samples showed a confident-looking
// "—" that was indistinguishable from a bug.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SiteCardView } from "../src/components/SiteCardView";
import type { SiteCard } from "../src/types";

function site(over: Partial<SiteCard> = {}): SiteCard {
  return {
    name: "shop", url: "https://shop.example", ok: true, code: 200, ms: 120,
    uptime_24h: 99.84, uptime_7d: 99.9,
    uptime_24h_checks: 288, uptime_7d_checks: 2016,
    uptime_24h_planned: 0, uptime_7d_planned: 0,
    latency_spark: [1, 2, 3], cert_days: 300, cert_expires: "2027-01-01",
    cert_issuer: "R3", dns_a: ["10.0.0.1"],
    headers: {
      hsts: true, nosniff: true, frame: true, csp: false,
      https_redirect: true, old_tls: false, server: "nginx",
    },
    incidents_7d: 0, public: true, stale: false, stale_note: "",
    last_checked: "2026-09-17T12:00:00-04:00",
    ...over,
  };
}

describe("what an uptime figure is made of", () => {
  it("shows the denominator beside the percentage", () => {
    render(<SiteCardView site={site()} certWarnDays={21} onOpen={() => {}} />);
    expect(screen.getByTestId("uptime-24h-checks")).toHaveTextContent("288 checks");
    expect(screen.getByTestId("uptime-7d-checks")).toHaveTextContent("2016 checks");
  });

  it("does not round a near miss up to a hit", () => {
    // 99.84 must survive the render as 99.84. `pct()` uses toFixed(2), which
    // rounds -- it is only safe because the hub already truncated. If the
    // hub ever sends more precision, this catches the double standard.
    render(<SiteCardView site={site({ uptime_24h: 99.84 })} certWarnDays={21} onOpen={() => {}} />);
    expect(screen.getByText("99.84%")).toBeInTheDocument();
  });

  it("says when a figure improved because maintenance was declared", () => {
    // An availability number that jumped with no explanation is one a
    // customer stops believing. The exclusion is named on the card so the
    // jump has a stated cause rather than looking like a correction.
    render(
      <SiteCardView
        site={site({ uptime_7d: 100, uptime_7d_checks: 1996, uptime_7d_planned: 20 })}
        certWarnDays={21}
        onOpen={() => {}}
      />,
    );
    expect(screen.getByTestId("uptime-7d-checks")).toHaveTextContent(
      "1996 checks · 20 planned, excluded",
    );
  });

  it("stays silent about maintenance when there was none", () => {
    render(<SiteCardView site={site()} certWarnDays={21} onOpen={() => {}} />);
    expect(screen.getByTestId("uptime-7d-checks")).not.toHaveTextContent("planned");
  });

  it("explains a missing percentage instead of leaving a bare dash", () => {
    render(
      <SiteCardView
        site={site({ uptime_24h: null, uptime_24h_checks: 6 })}
        certWarnDays={21}
        onOpen={() => {}}
      />,
    );
    expect(screen.getByTestId("uptime-24h-checks")).toHaveTextContent("6 checks — too few");
  });
});
