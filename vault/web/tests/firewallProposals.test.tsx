// Item 21: gateway rules Vault proposes, and never applies on its own.
//
// Miro's ruling is the shape of the panel: propose, one-click apply, timed
// auto-rollback, never a silent write to a router. The tests that matter
// are about restraint — that Apply is honestly dead while there is nothing
// to apply to, and that "No" is a decision rather than a dismissal.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import { FirewallProposals } from "../src/components/FirewallProposals";
import type { FirewallPayload, FirewallRule } from "../src/types";

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, firewallRules: vi.fn(), declineFirewallRule: vi.fn() };
});
import { declineFirewallRule, firewallRules } from "../src/lib/api";

afterEach(() => vi.restoreAllMocks());

function rule(over: Partial<FirewallRule> = {}): FirewallRule {
  return {
    id: "r1", key: "block_inbound:tcp:3389", action: "block_inbound", proto: "tcp",
    port: 3389, target: "203.0.113.9",
    title: "Block inbound Remote Desktop (port 3389) at the router",
    because: "Port 3389 is reachable from the internet. Remote Desktop is the single most brute-forced service on the internet.",
    evidence: "Seen open from outside at 203.0.113.9:3389 on 2026-09-22T10:00:00",
    risk: "CRIT", status: "proposed", decided_by: "", decided_at: "", reason: "",
    applied_at: "", rollback_at: "", first_seen: "", last_seen: "", ...over,
  };
}

function mount(payload: Partial<FirewallPayload> = {}) {
  vi.mocked(firewallRules).mockResolvedValue({
    rules: [rule()], can_apply: false,
    why_not: "Vault has no write access to this gateway yet.",
    gateway: "192.168.1.1", ...payload,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider><FirewallProposals /></ToastProvider>
    </QueryClientProvider>,
  );
}

describe("proposed firewall rules", () => {
  it("shows the rule with the evidence it came from", async () => {
    mount();
    const panel = await screen.findByTestId("firewall-proposals");
    expect(panel.textContent).toContain("Remote Desktop");
    expect(panel.textContent).toContain("203.0.113.9:3389");
    // A rule a customer cannot trace back to an observation is one they
    // either apply blindly or ignore. Both are worse than no rule.
    expect(panel.textContent).toContain("most brute-forced service");
  });

  it("Apply is dead, and says why, while there is nothing to apply to", async () => {
    mount();
    const panel = await screen.findByTestId("firewall-proposals");
    const apply = within(panel).getByRole("button", { name: "Apply" });
    expect(apply).toBeDisabled();
    expect(apply.getAttribute("title")).toContain("no write access");
    expect(screen.getByTestId("firewall-cant-apply").textContent).toContain("no write access");
  });

  it("Apply comes alive only when the hub says it can", async () => {
    mount({ can_apply: true, why_not: "" });
    const panel = await screen.findByTestId("firewall-proposals");
    expect(within(panel).getByRole("button", { name: "Apply" })).not.toBeDisabled();
  });

  it("leaving a rule alone records a reason, and is not a dismissal", async () => {
    mount();
    vi.mocked(declineFirewallRule).mockResolvedValue(rule({ status: "declined" }));
    const panel = await screen.findByTestId("firewall-proposals");
    await userEvent.click(within(panel).getByRole("button", { name: "Leave it" }));
    await userEvent.type(screen.getByLabelText(/Why leave/), "our own jump host");
    await userEvent.click(screen.getByRole("button", { name: "Leave it" }));
    await waitFor(() => expect(declineFirewallRule).toHaveBeenCalledWith("r1", "our own jump host"));
  });

  it("a decision already made is shown as one, not re-asked", async () => {
    mount({ rules: [rule({ status: "declined", decided_by: "miro", reason: "our own jump host" })] });
    const panel = await screen.findByTestId("firewall-proposals");
    expect(panel.textContent).toContain("miro left it as it is");
    expect(within(panel).queryByRole("button", { name: "Apply" })).toBeNull();
  });

  it("a finding that has gone away stays listed as settled", async () => {
    // "This was open last week and is not now" is what an auditor asks
    // about, and a row that vanishes cannot answer.
    mount({ rules: [rule({ status: "expired" })] });
    const panel = await screen.findByTestId("firewall-proposals");
    expect(panel.textContent).toContain("No longer open");
  });

  it("says nothing at all when Vault found nothing to propose", async () => {
    mount({ rules: [] });
    await waitFor(() => expect(firewallRules).toHaveBeenCalled());
    expect(screen.queryByTestId("firewall-proposals")).toBeNull();
  });
});
