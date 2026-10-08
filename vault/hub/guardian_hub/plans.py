"""The plan catalogue -- the ONE place a Vault price is written down.

Every other surface derives from this module: the hub's `/api/v1/billing`
payload, the Stripe price lookup keys, the in-app Billing page (which renders
what the API sends, never its own numbers), and -- via
`tests/test_billing_plans.py`, which parses `site/index.html` -- the public
pricing section too. A price that exists in only one of those places is the
money bug this module exists to make impossible.

Rules that hold here and must keep holding:

* **Minor units, integers, always.** `monthly_cents` / `yearly_cents` are
  ints. No float ever touches a price: 0.1 + 0.2 reconciled against a Stripe
  invoice is a support ticket nobody enjoys.
* **Yearly is twelve months for the price of ten.** `yearly_cents ==
  monthly_cents * 10`, asserted at import time below, so a hand-edited
  yearly price that drifts off the convention fails the build rather than
  quietly undercharging. The owner has not confirmed this discount; see the
  task report.
* **The trial is its own plan, not a flag on a paid one.** It costs nothing,
  takes no card, creates no Stripe object, and does not convert. Its `id`
  sits in this same table so that "what plan is this tenant on?" has exactly
  one answer type everywhere.
* **`lookup_key` is the contract with Stripe.** Prices are resolved by
  lookup key (`guardian_home_month`, ...) rather than by a price id pasted
  into the environment, so the owner creates them once in the Stripe
  dashboard and nothing here needs re-deploying when he does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Currency for every paid plan. USD throughout: the paid prices the owner
# quoted are dollars, and a zero-cost trial shows no currency at all.
CURRENCY = "usd"

# How long the free trial runs. Three months, expressed in days because that
# is the only unit a date subtraction can be exact in -- 90 days is the
# number the in-app countdown and the expiry job both use.
TRIAL_DAYS = 90

# The twelve-for-ten convention, in one constant so the report and the tests
# can quote the same number the prices were built from.
YEARLY_MONTHS_CHARGED = 10

PlanId = Literal["trial", "home", "business_essentials", "business_pro", "partner"]

# Interval of a *paid* subscription. The trial has no billing interval at
# all, which is `"none"` rather than a null so the column and the TS union
# stay non-optional.
BillingInterval = Literal["month", "year", "none"]


@dataclass(frozen=True)
class Plan:
    """One purchasable (or, for the trial, claimable) subscription."""

    id: str
    name: str
    blurb: str
    #: 0 for the trial. Minor units (cents).
    monthly_cents: int
    yearly_cents: int
    #: True when the price is charged per monitored site rather than per
    #: account -- Business Essentials and Partner/MSP. Display-only: the hub
    #: does not meter sites, Stripe's quantity does.
    per_site: bool = False
    #: True for the one plan that takes no payment method.
    is_trial: bool = False

    def lookup_key(self, interval: str) -> str:
        """The Stripe price lookup key for this plan at this interval."""
        return f"guardian_{self.id}_{interval}"

    def cents(self, interval: str) -> int:
        if interval == "year":
            return self.yearly_cents
        if interval == "month":
            return self.monthly_cents
        return 0


def _yearly(monthly: int) -> int:
    return monthly * YEARLY_MONTHS_CHARGED


TRIAL = Plan(
    id="trial",
    name="Free trial",
    blurb="The whole product for three months: sensor, dashboard, alerts and "
          "automatic repairs. No card, and nothing converts on its own.",
    monthly_cents=0,
    yearly_cents=0,
    is_trial=True,
)

PLANS: tuple[Plan, ...] = (
    TRIAL,
    Plan(
        id="home",
        name="Home",
        blurb="One home network: the devices on it, who is around, and what "
              "your gear is talking to.",
        monthly_cents=900,
        yearly_cents=_yearly(900),
    ),
    Plan(
        id="business_essentials",
        name="Business Essentials",
        blurb="One office or shop: the network plus your servers, websites "
              "and certificates, with alerts that reach the right person.",
        monthly_cents=4900,
        yearly_cents=_yearly(4900),
        per_site=True,
    ),
    Plan(
        id="business_pro",
        name="Business Pro",
        blurb="For a site you cannot afford to have down: automatic repairs, "
              "the assistant, deeper history and scheduled reports.",
        monthly_cents=14900,
        yearly_cents=_yearly(14900),
    ),
    Plan(
        id="partner",
        name="Partner / MSP",
        blurb="Many client sites in one place, white-labelled -- your name "
              "and your colours on the dashboard and the reports.",
        monthly_cents=9900,
        yearly_cents=_yearly(9900),
        per_site=True,
    ),
)

BY_ID: dict[str, Plan] = {p.id: p for p in PLANS}

#: The four plans a customer can actually be charged for.
PAID_PLANS: tuple[Plan, ...] = tuple(p for p in PLANS if not p.is_trial)

INTERVALS: tuple[str, ...] = ("month", "year")


def get(plan_id: str) -> Plan | None:
    return BY_ID.get(plan_id)


def monthly_cents(plan_id: str, interval: str, quantity: int = 1) -> int:
    """What one subscription contributes to monthly recurring revenue.

    A yearly subscription is NOT worth its sticker price per month. The
    twelve-for-ten convention above means a year costs `monthly * 10`, so its
    monthly-equivalent is `monthly * 10 / 12` -- five sixths of the monthly
    price. Summing yearly plans at `monthly_cents`, or at `yearly_cents`,
    overstates MRR by 20% or by 1100%; both mistakes look plausible on a page
    with one customer on it and neither survives contact with a real ledger.

    Integer cents throughout, rounded half-up at the last step rather than
    truncated, because truncating every row biases the total downward by up to
    a cent per subscription -- invisible at two customers and a real drift at
    two hundred.

    `quantity` is the site count for a per-site plan. The hub does not meter
    sites and the subscription row does not store the quantity Stripe was
    given, so the caller passes what it knows and the figure is a valuation of
    the plans customers are on, not a reading of their invoices. The Billing
    page says so in as many words; do not quietly promote it to "revenue
    collected".
    """
    plan = BY_ID.get(plan_id)
    if plan is None or plan.is_trial or quantity <= 0:
        return 0
    if interval == "month":
        return plan.monthly_cents * quantity
    if interval == "year":
        return (plan.yearly_cents * quantity * 2 + 12) // 24
    # "none" -- a trial or an abandoned checkout placeholder. Not revenue.
    return 0


def price_label(plan: Plan, interval: str) -> str:
    """Human price, e.g. "$49/site/mo" or "$490/site/yr". Display only."""
    if plan.is_trial:
        return "Free"
    cents = plan.cents(interval)
    whole, rem = divmod(cents, 100)
    amount = f"${whole}" if rem == 0 else f"${whole}.{rem:02d}"
    site = "/site" if plan.per_site else ""
    return f"{amount}{site}/{'yr' if interval == 'year' else 'mo'}"


# --- invariants, checked at import so a bad edit cannot ship -------------
for _p in PAID_PLANS:
    assert _p.monthly_cents > 0, f"{_p.id}: paid plan needs a monthly price"
    assert _p.yearly_cents == _p.monthly_cents * YEARLY_MONTHS_CHARGED, (
        f"{_p.id}: yearly price is off the twelve-for-ten convention"
    )
assert TRIAL.monthly_cents == 0 and TRIAL.yearly_cents == 0
assert len(PLANS) == 5, "five subscriptions: the trial plus four paid plans"
del _p
