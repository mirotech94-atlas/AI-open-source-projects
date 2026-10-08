import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CertsExpiringPanel, CoveragePanel } from "../src/components/CoveragePanel";

/**
 * The Certificates-expiring card has three empty states, and only one of
 * them is good news. The first version printed the good one regardless --
 * "nothing expiring" -- while the Coverage card beside it read 0 / 3, meaning
 * Vault had not read a certificate for ANY site. Silence about
 * certificates is the absence of the check, not a clean bill, and reporting it
 * as safety is the exact failure `payloads/staleness` exists to stop.
 */
describe("certificates expiring, with nothing to list", () => {
  it("says so plainly when Vault has read no certificate at all", () => {
    render(<CertsExpiringPanel rows={[]} read={0} sites={3} />);
    expect(screen.getByText(/has not read a certificate for any of your 3 sites/i)).toBeInTheDocument();
    expect(screen.getByText(/not a clean bill/i)).toBeInTheDocument();
    // The reassuring line must NOT appear: that is the bug this test pins.
    expect(screen.queryByText(/nothing expiring in the next 90 days\./i)).toBeNull();
  });

  it("qualifies the good news when only some sites have a reading", () => {
    render(<CertsExpiringPanel rows={[]} read={2} sites={3} />);
    expect(screen.getByText(/among the 2 of 3 sites/i)).toBeInTheDocument();
    expect(screen.getByText(/the other 1 is unread/i)).toBeInTheDocument();
  });

  it("gives the clean bill only when every site has been read", () => {
    render(<CertsExpiringPanel rows={[]} read={3} sites={3} />);
    expect(screen.getByText(/nothing expiring in the next 90 days\. vault renews/i)).toBeInTheDocument();
  });

  it("colours a certificate under a fortnight as an emergency, and under a month as work", () => {
    render(<CertsExpiringPanel read={2} sites={2} rows={[
      { host: "soon.example", issuer: "Let's Encrypt R11", days_left: 9 },
      { host: "later.example", issuer: "Let's Encrypt R11", days_left: 27 },
    ]} />);
    expect(screen.getByText("9")).toHaveClass("g-cert-crit");
    expect(screen.getByText("27")).toHaveClass("g-cert-warn");
  });
});

describe("coverage", () => {
  it("draws short coverage in the warning tone and full coverage quietly", () => {
    render(<CoveragePanel rows={[
      { label: "Devices", seen: 20, total: 26 },
      { label: "Websites", seen: 3, total: 3 },
    ]} />);
    expect(screen.getByText("77%")).toHaveClass("g-cov-short");
    expect(screen.getByText("100%")).not.toHaveClass("g-cov-short");
  });
});
