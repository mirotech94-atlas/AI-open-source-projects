import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ThreatMatrix } from "../src/components/ThreatMatrix";
import type { ThreatClass } from "../src/types";

// The grid answers "what about phishing / DDoS / ransomware". The failure
// mode is not an ugly card, it is a flattering one: a card that reads as
// protection when Vault only watches, or only reduces the odds.

function t(over: Partial<ThreatClass> = {}): ThreatClass {
  return {
    key: "phishing", label: "Phishing", what: "Fake login pages.",
    stance: "blocking", does: "Blocked by the DNS filter.", limit: "",
    open_count: 0, blocked: 12, signals: [], ...over,
  };
}

describe("threat matrix", () => {
  it("says which attacks Vault stops and which it only watches", () => {
    render(<ThreatMatrix threats={[
      t(),
      t({ key: "dos", label: "Denial of service", stance: "effect-only", blocked: 0 }),
      t({ key: "ransomware", label: "Ransomware", stance: "indirect", blocked: 0 }),
    ]} />);
    expect(screen.getByText("Blocked")).toBeInTheDocument();
    expect(screen.getByText("Damage only")).toBeInTheDocument();
    expect(screen.getByText("Odds reduced")).toBeInTheDocument();
  });

  it("shows what Vault cannot do, next to what it can", () => {
    render(<ThreatMatrix threats={[t({
      key: "dos", label: "Denial of service", stance: "effect-only",
      does: "Tells you when a site stops answering.",
      limit: "Vault cannot absorb or filter an attack.",
    })]} />);
    expect(screen.getByText(/cannot absorb or filter/)).toBeInTheDocument();
  });

  it("a quiet class reads as quiet, not as an achievement", () => {
    render(<ThreatMatrix threats={[t({ blocked: 0 })]} />);
    expect(screen.getByText("Nothing open")).toBeInTheDocument();
  });

  it("counts blocks only when there are some", () => {
    render(<ThreatMatrix threats={[t({ blocked: 1204 })]} />);
    expect(screen.getByText(/1,204 blocked this week/)).toBeInTheDocument();
  });

  it("lists the open signals behind a class rather than just a number", () => {
    render(<ThreatMatrix threats={[t({
      key: "mitm", label: "Man in the middle", stance: "watching", open_count: 2,
      signals: [
        { key: "home:route", title: "Default route is not the router", severity: "CRIT" },
        { key: "dns:shop", title: "shop DNS changed", severity: "WARN" },
      ],
    })]} />);
    expect(screen.getByText("2 open signals")).toBeInTheDocument();
    expect(screen.getByText("Default route is not the router")).toBeInTheDocument();
    expect(screen.getByText("CRIT")).toBeInTheDocument();
  });

  it("marks a card that has something open", () => {
    render(<ThreatMatrix threats={[t({ open_count: 1, signals: [
      { key: "sec:miner:vps1", title: "crypto-miner running", severity: "CRIT" }] })]} />);
    const card = document.querySelector(".g-threat") as HTMLElement;
    expect(card.dataset.open).toBe("1");
    expect(within(card).getByText("1 open signal")).toBeInTheDocument();
  });

  it("renders nothing rather than an empty heading when there is no data", () => {
    const { container } = render(<ThreatMatrix threats={[]} />);
    expect(container.querySelector(".g-threats")).toBeNull();
  });
});

// --- the drawer -----------------------------------------------------------
//
// A card states a verdict; opening it shows the evidence for that verdict.
// The failure mode here is the same as the grid's and one level worse: a
// drawer that shows only what is still open reads as "Vault dealt with
// nothing", and a drawer that drops the `limit` line turns the honest card
// into a misleading popup.

import userEvent from "@testing-library/user-event";
import type { ThreatDetail, ThreatFinding } from "../src/types";

function finding(over: Partial<ThreatFinding> = {}): ThreatFinding {
  return {
    key: "email:spf:missing", title: "SPF missing", severity: "WARN",
    why: "Nothing publishes an SPF record.", evidence: {}, status: "open",
    resolved: false, fix: "", resolved_by: "", first_seen: "", last_seen: "",
    count: 1, ...over,
  };
}

function detail(over: Partial<ThreatDetail> = {}): ThreatDetail {
  return {
    key: "phishing", label: "Phishing", what: "Fake login pages.",
    stance: "blocking", does: "Blocked by the DNS filter.", limit: "",
    days: 7, open_count: 1, resolved_count: 0, blocked: 12,
    findings: [finding()], ...over,
  };
}

describe("threat drawer", () => {
  it("opens when a card is clicked and asks for that class", async () => {
    const onOpen = vi.fn();
    render(<ThreatMatrix threats={[t()]} onOpen={onOpen} />);
    await userEvent.click(screen.getByRole("button", { name: /phishing/i }));
    expect(onOpen).toHaveBeenCalledWith("phishing");
  });

  it("shows how a settled alert was settled", async () => {
    // The whole point. The card cannot answer this — its signals are open
    // alerts only, so a settled alert never appears there.
    render(
      <ThreatMatrix
        threats={[t()]}
        detail={detail({
          open_count: 0, resolved_count: 1,
          findings: [finding({ status: "resolved", resolved: true, fix: "dns_record_add", title: "DMARC not enforced" })],
        })}
      />,
    );
    expect(screen.getByText(/dns_record_add/)).toBeInTheDocument();
  });

  it("says a settled alert had no repair rather than implying one", async () => {
    // A person deciding an alert needs no fix is a different story from
    // Vault having repaired something, and the drawer must not merge them.
    render(
      <ThreatMatrix
        threats={[t()]}
        detail={detail({
          findings: [finding({ status: "acked", resolved: true, fix: "", resolved_by: "miro" })],
        })}
      />,
    );
    expect(screen.getByText(/no repair|marked it|without a repair/i)).toBeInTheDocument();
  });

  it("keeps the admission that Vault cannot stop this", async () => {
    render(
      <ThreatMatrix
        threats={[t({ key: "dos", label: "Denial of service", stance: "effect-only" })]}
        detail={detail({
          key: "dos", label: "Denial of service", stance: "effect-only",
          limit: "Vault cannot absorb or filter an attack.",
          findings: [],
        })}
      />,
    );
    expect(screen.getByText(/cannot absorb or filter/i)).toBeInTheDocument();
  });

  it("never adds blocked lookups to the findings count", async () => {
    // `blocked` is a DNS count over the window; findings are alerts. A drawer
    // that summed them would print a number that means nothing.
    render(
      <ThreatMatrix threats={[t()]} detail={detail({ blocked: 12, findings: [finding()] })} />,
    );
    expect(screen.queryByText(/\b13\b/)).toBeNull();
  });
});
