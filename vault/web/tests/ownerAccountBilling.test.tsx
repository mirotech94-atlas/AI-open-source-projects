import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import Account from "../src/owner/Account";
import { LIVE_EVENT_TYPES } from "../src/types";
import type { AccountBilling, AccountPayload } from "../src/types";

// The console's billing control. What matters: the owner can extend a trial
// or grant a plan in two clicks and see the result without a reload; a
// customer Stripe is billing is sent to Stripe rather than edited here; and
// nothing on this page pretends a comped plan is revenue.

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    accountPayload: vi.fn(),
    accountTrends: vi.fn(),
    adjustAccountBilling: vi.fn(),
  };
});

const api = await import("../src/lib/api");

function billing(over: Partial<AccountBilling> = {}): AccountBilling {
  return {
    plan: "trial", plan_label: "Free trial", interval: "none", status: "trialing",
    mrr_cents: 0, per_site: false, trial_days_left: 12, period_end: "",
    cancel_at_period_end: false, stripe_customer_id: "", stripe_dashboard_url: "",
    ...over,
  };
}

function payload(b: AccountBilling | null): AccountPayload {
  return {
    id: "t2", slug: "northwind", name: "Northwind Dental",
    created: "", networks: [], devices_unassigned: 0, sensors: [], users: [], integrations: [], recent: [],
    billing: b,
  } as unknown as AccountPayload;
}

function mount(p: AccountPayload) {
  vi.mocked(api.accountPayload).mockResolvedValue(p);
  vi.mocked(api.accountTrends).mockResolvedValue({ days: 30, trends: [] } as never);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/owner/accounts/t2"]}>
          <Routes><Route path="/owner/accounts/:tid" element={<Account />} /></Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe("owner account billing control", () => {
  it("extends a trial in two clicks and shows the new number without a reload", async () => {
    mount(payload(billing()));
    const section = await screen.findByRole("region", { name: /billing/i });
    vi.mocked(api.adjustAccountBilling).mockResolvedValue(payload(billing({ trial_days_left: 42 })));

    await userEvent.click(within(section).getByRole("button", { name: /extend trial/i }));
    // A second, explicit click: nothing on this page changes what a customer
    // gets on one press.
    await userEvent.click(within(section).getByRole("button", { name: /confirm/i }));

    expect(api.adjustAccountBilling).toHaveBeenCalledWith("t2", { action: "extend_trial", days: 14 });
    await waitFor(() => expect(within(section).getByText(/42d left/)).toBeInTheDocument());
  });

  it("grants a plan with the chosen interval, and a comp reads as nothing owed", async () => {
    mount(payload(billing()));
    const section = await screen.findByRole("region", { name: /billing/i });
    vi.mocked(api.adjustAccountBilling).mockResolvedValue(
      payload(billing({ plan: "partner", plan_label: "Partner / MSP", interval: "month", status: "active", trial_days_left: null, mrr_cents: 0 })),
    );

    await userEvent.selectOptions(within(section).getByLabelText(/grant plan/i), "partner");
    await userEvent.selectOptions(within(section).getByLabelText(/billed/i), "year");
    await userEvent.click(within(section).getByRole("button", { name: /grant/i }));
    await userEvent.click(within(section).getByRole("button", { name: /confirm/i }));

    expect(api.adjustAccountBilling).toHaveBeenCalledWith("t2", { action: "grant_plan", plan: "partner", interval: "year" });
    // The facts list, not the select (which also offers "Partner / MSP").
    await waitFor(() =>
      expect(within(section).getAllByRole("definition").map((d) => d.textContent)).toContain("Partner / MSP"),
    );
    expect(within(section).getByText(/nothing is billed for this plan/i)).toBeInTheDocument();
  });

  it("sends a Stripe-billed customer to Stripe instead of offering to edit them", async () => {
    mount(payload(billing({
      plan: "home", plan_label: "Vault Home", interval: "month", status: "active",
      trial_days_left: null, mrr_cents: 900, stripe_customer_id: "cus_9",
      stripe_dashboard_url: "https://dashboard.stripe.com/customers/cus_9",
    })));
    const section = await screen.findByRole("region", { name: /billing/i });

    const link = within(section).getByRole("link", { name: /manage in stripe/i });
    expect(link).toHaveAttribute("href", "https://dashboard.stripe.com/customers/cus_9");
    expect(within(section).queryByRole("button", { name: /extend trial/i })).toBeNull();
    expect(within(section).queryByRole("button", { name: /grant/i })).toBeNull();
  });

  it("shows the refusal Stripe-billed customers get, in the owner's words, if the hub says no", async () => {
    mount(payload(billing()));
    const section = await screen.findByRole("region", { name: /billing/i });
    vi.mocked(api.adjustAccountBilling).mockRejectedValue(
      new Error("This customer is billed by Stripe (sub_9); change their plan there: https://dashboard.stripe.com/customers/cus_9"),
    );
    await userEvent.click(within(section).getByRole("button", { name: /extend trial/i }));
    await userEvent.click(within(section).getByRole("button", { name: /confirm/i }));
    await waitFor(() => expect(screen.getByText(/billed by Stripe/i)).toBeInTheDocument());
  });

  it("the customer's own Billing page can hear about it: 'billing' is a live event type", () => {
    expect(LIVE_EVENT_TYPES).toContain("billing");
  });
});
