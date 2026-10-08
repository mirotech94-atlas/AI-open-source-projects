import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import Settings from "../src/pages/Settings";
import type { MeOut } from "../src/api";
import type { BillingPayload, SettingsPayload } from "../src/types";

// Settings > Billing. What is asserted here is the two things a money page
// gets wrong most easily: showing a price the hub did not send, and offering
// a button that cannot do what it says.

const assign = vi.fn();

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    settingsPayload: vi.fn(),
    billingPayload: vi.fn(),
    startTrial: vi.fn(),
    billingCheckout: vi.fn(),
    billingPortal: vi.fn(),
  };
});

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return { ...actual, me: vi.fn() };
});

const api = await import("../src/lib/api");
const rootApi = await import("../src/api");

const EMPTY_SETTINGS: SettingsPayload = {
  suggested_sites: [],
  profile: { owner: "", email: "", address: "", company: "" },
  users: [],
  sites: [],
  hosts: [],
  autofix: {
    enabled: true,
    max_per_day: 0,
    attacker_method: "auto",
    maintenance_window: { start: "02:00", end: "05:00" },
    cooldowns: {},
  },
  thresholds: {},
  appearance: { theme: "system", accent: "blue" },
  sensors: [],
  tenants: [],
};

function meOut(role: string): MeOut {
  const tenant = { id: "t1", slug: "home", name: "Home", role };
  return {
    user: { username: "miro", display_name: "Miro", is_superadmin: false },
    tenants: [tenant],
    active_tenant: tenant,
    chat_providers: [],
  };
}

// The prices the hub would send, from `plans.py`. Written out rather than
// generated: a fixture that computes its own numbers proves nothing about
// whether the component renders the ones it was given.
function payload(over: Partial<BillingPayload> = {}): BillingPayload {
  return {
    configured: true,
    currency: "usd",
    trial_days: 90,
    yearly_months_charged: 10,
    plans: [
      { id: "trial", name: "Free trial", blurb: "Everything, for three months.",
        monthly_cents: 0, yearly_cents: 0, monthly_label: "Free", yearly_label: "Free",
        per_site: false, is_trial: true, current: false },
      { id: "home", name: "Home", blurb: "One home network.",
        monthly_cents: 900, yearly_cents: 9000, monthly_label: "$9/mo", yearly_label: "$90/yr",
        per_site: false, is_trial: false, current: false },
      { id: "business_essentials", name: "Business Essentials", blurb: "One office or shop.",
        monthly_cents: 4900, yearly_cents: 49000, monthly_label: "$49/site/mo",
        yearly_label: "$490/site/yr", per_site: true, is_trial: false, current: false },
      { id: "business_pro", name: "Business Pro", blurb: "A site you cannot have down.",
        monthly_cents: 14900, yearly_cents: 149000, monthly_label: "$149/mo",
        yearly_label: "$1490/yr", per_site: false, is_trial: false, current: false },
      { id: "partner", name: "Partner / MSP", blurb: "Many client sites, white-labelled.",
        monthly_cents: 9900, yearly_cents: 99000, monthly_label: "$99/site/mo",
        yearly_label: "$990/site/yr", per_site: true, is_trial: false, current: false },
    ],
    subscription: null,
    notice: "",
    portal_available: false,
    ...over,
  };
}

function renderBilling(node: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/settings/billing"]}>
          <Routes>
            <Route path="/settings/:section" element={node} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(rootApi.me).mockResolvedValue(meOut("admin"));
  vi.mocked(api.settingsPayload).mockResolvedValue(EMPTY_SETTINGS);
  vi.mocked(api.billingPayload).mockResolvedValue(payload());
  vi.stubGlobal("location", { ...window.location, assign });
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("Settings > Billing", () => {
  it("shows all five plans and switches every price with the interval toggle", async () => {
    renderBilling(<Settings />);
    await screen.findByText("$9/mo");

    for (const label of ["$49/site/mo", "$149/mo", "$99/site/mo", "Free"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByRole("heading", { name: "Free trial" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Yearly/ }));
    for (const label of ["$90/yr", "$490/site/yr", "$1490/yr", "$990/site/yr"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // And the monthly prices are gone, not merely joined by the yearly ones.
    expect(screen.queryByText("$9/mo")).not.toBeInTheDocument();
    // The saving the hub reported, not a number this component invented.
    expect(screen.getByText(/2 months free/)).toBeInTheDocument();
  });

  it("renders the trial with its end date and days left, and no card language", async () => {
    const ends = new Date(Date.now() + 12 * 86400_000).toISOString();
    vi.mocked(api.billingPayload).mockResolvedValue(
      payload({
        subscription: {
          plan: "trial", plan_name: "Free trial", interval: "none", status: "trialing",
          price_label: "Free", trial_end: ends, trial_days_left: 12,
          current_period_end: "", cancel_at_period_end: false, ingesting: true,
        },
        notice: "Your free trial ends in 12 days. Pick a plan to keep collecting new data.",
      }),
    );
    renderBilling(<Settings />);
    expect(await screen.findByText(/ends in 12 days/)).toBeInTheDocument();
    expect(screen.getByText(/12 days left/)).toBeInTheDocument();
    expect(screen.getByText("Reporting normally.")).toBeInTheDocument();
    // The trial cannot be claimed twice.
    expect(screen.getByRole("button", { name: "Start the free trial" })).toBeDisabled();
  });

  it("says a failed payment has not touched the monitoring", async () => {
    vi.mocked(api.billingPayload).mockResolvedValue(
      payload({
        subscription: {
          plan: "home", plan_name: "Home", interval: "month", status: "past_due",
          price_label: "$9/mo", trial_end: "", trial_days_left: null,
          current_period_end: "2026-10-01T00:00:00+00:00", cancel_at_period_end: false,
          ingesting: true,
        },
        notice: "The last payment did not go through. Monitoring is unaffected.",
        portal_available: true,
      }),
    );
    renderBilling(<Settings />);
    expect(await screen.findByText(/Monitoring is unaffected/)).toBeInTheDocument();
    expect(screen.getByText("Payment failed")).toBeInTheDocument();
    expect(screen.getByText("Reporting normally.")).toBeInTheDocument();
  });

  it("says a paused account kept its data", async () => {
    vi.mocked(api.billingPayload).mockResolvedValue(
      payload({
        subscription: {
          plan: "trial", plan_name: "Free trial", interval: "none", status: "paused",
          price_label: "Free", trial_end: "2026-06-01T00:00:00+00:00", trial_days_left: null,
          current_period_end: "", cancel_at_period_end: false, ingesting: false,
        },
        notice: "Your free trial has ended, so the sensors have stopped reporting.",
      }),
    );
    renderBilling(<Settings />);
    expect(await screen.findByText("Paused")).toBeInTheDocument();
    expect(
      screen.getByText(/Everything already collected is still here/),
    ).toBeInTheDocument();
  });

  it("confirms before handing off to Stripe, and sends the plan the admin picked", async () => {
    vi.mocked(api.billingCheckout).mockResolvedValue({ url: "https://checkout.stripe.test/c/1" });
    renderBilling(<Settings />);
    await screen.findByText("$9/mo");

    await userEvent.click(screen.getByRole("button", { name: /Yearly/ }));
    await userEvent.click(screen.getByRole("button", { name: "Choose Business Pro" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/\$1490\/yr/)).toBeInTheDocument();
    // Nothing has been sent yet — the dialog is a real gate, not decoration.
    expect(api.billingCheckout).not.toHaveBeenCalled();

    await userEvent.click(within(dialog).getByRole("button", { name: "Continue to Stripe" }));
    await waitFor(() => expect(api.billingCheckout).toHaveBeenCalledWith("business_pro", "year", 1));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://checkout.stripe.test/c/1"));
  });

  it("sends the site count for a per-site plan, from a field inside the dialog", async () => {
    // The regression this guards: the field used to render beside the dialog,
    // behind its backdrop, and the dialog traps focus between its own
    // controls — so it could never be reached and every per-site checkout
    // silently went through at one site. An MSP buying twenty was billed for
    // one.
    vi.mocked(api.billingCheckout).mockResolvedValue({ url: "https://checkout.stripe.test/c/2" });
    renderBilling(<Settings />);
    await screen.findByText("$9/mo");

    await userEvent.click(screen.getByRole("button", { name: "Choose Business Essentials" }));
    const dialog = await screen.findByRole("alertdialog");

    const count = within(dialog).getByLabelText(/how many sites/i);
    await userEvent.clear(count);
    await userEvent.type(count, "4");
    expect(dialog.textContent).toMatch(/for 4 sites/);

    await userEvent.click(within(dialog).getByRole("button", { name: "Continue to Stripe" }));
    await waitFor(() =>
      expect(api.billingCheckout).toHaveBeenCalledWith("business_essentials", "month", 4),
    );
  });

  it("does not carry a site count over into the next plan's checkout", async () => {
    vi.mocked(api.billingCheckout).mockResolvedValue({ url: "https://checkout.stripe.test/c/3" });
    renderBilling(<Settings />);
    await screen.findByText("$9/mo");

    await userEvent.click(screen.getByRole("button", { name: "Choose Partner / MSP" }));
    let dialog = await screen.findByRole("alertdialog");
    const count = within(dialog).getByLabelText(/how many sites/i);
    await userEvent.clear(count);
    await userEvent.type(count, "12");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await userEvent.click(screen.getByRole("button", { name: "Choose Business Essentials" }));
    dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByLabelText(/how many sites/i)).toHaveValue(1);
    await userEvent.click(within(dialog).getByRole("button", { name: "Continue to Stripe" }));
    await waitFor(() =>
      expect(api.billingCheckout).toHaveBeenCalledWith("business_essentials", "month", 1),
    );
  });

  it("offers no checkout at all when the hub has no Stripe keys", async () => {
    vi.mocked(api.billingPayload).mockResolvedValue(payload({ configured: false }));
    renderBilling(<Settings />);
    expect(await screen.findByText(/not switched on for this hub yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose Home" })).toBeDisabled();
    // The trial is entirely local, so it is still on offer.
    expect(screen.getByRole("button", { name: "Start the free trial" })).toBeEnabled();
  });

  it("is read-only for an operator, with the reason said out loud", async () => {
    vi.mocked(rootApi.me).mockResolvedValue(meOut("operator"));
    renderBilling(<Settings />);
    await screen.findByText("$9/mo");
    expect(screen.getByText(/admin access is required/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose Home" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Start the free trial" })).toBeDisabled();
  });

  // --- trial-only hub (the shipped default) --------------------------------

  const TRIAL_ONLY = {
    trial_only: true,
    plans: [
      { id: "trial", name: "Free trial", blurb: "Everything, for three months.",
        monthly_cents: 0, yearly_cents: 0, monthly_label: "Free", yearly_label: "Free",
        per_site: false, is_trial: true, current: false },
    ],
  };

  it("offers the trial and no paid plans at all when the hub is trial-only", async () => {
    vi.mocked(api.billingPayload).mockResolvedValue(payload(TRIAL_ONLY));
    renderBilling(<Settings />);
    expect(await screen.findByTestId("billing-trial-only")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start the free trial" })).toBeEnabled();
    // No tier is on offer, and no price from the catalogue is quoted.
    for (const name of ["Home", "Business Essentials", "Business Pro", "Partner / MSP"]) {
      expect(screen.queryByRole("button", { name: `Choose ${name}` })).toBeNull();
    }
    for (const price of ["$9/mo", "$49/site/mo", "$149/mo", "$99/site/mo"]) {
      expect(screen.queryByText(price)).toBeNull();
    }
    expect(screen.queryByRole("group", { name: "Billing interval" })).toBeNull();
  });

  it("says the trial still ends, rather than implying the product is free forever", async () => {
    vi.mocked(api.billingPayload).mockResolvedValue(
      payload({
        ...TRIAL_ONLY,
        subscription: {
          plan: "trial", plan_name: "Free trial", interval: "month", status: "trialing",
          price_label: "Free", trial_end: "2026-12-01T00:00:00+00:00", trial_days_left: 12,
          current_period_end: "", cancel_at_period_end: false, ingesting: true,
        },
      }),
    );
    renderBilling(<Settings />);
    expect(await screen.findByTestId("billing-trial-only")).toBeInTheDocument();
    expect(screen.getByText(/12 days left/)).toBeInTheDocument();
    expect(screen.getByText(/The account pauses/)).toBeInTheDocument();
    // Already on the trial: nothing left to start.
    expect(screen.queryByRole("button", { name: "Start the free trial" })).toBeNull();
  });
});
