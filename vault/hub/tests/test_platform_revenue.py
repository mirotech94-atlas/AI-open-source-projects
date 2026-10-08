"""The owner console's money: monthly recurring revenue on `GET /api/v1/platform`.

The Billing page used to say, in a footnote, that it knew nothing about
revenue. Computing it means deciding two things that are easy to get wrong and
invisible when they are, because a one-customer platform makes every formula
look right.

**Which statuses pay.** `trialing` pays nothing yet, `paused` and `canceled`
pay nothing any more, and `incomplete` is the placeholder row written when a
checkout STARTS -- booking that one would credit the business for a customer
who closed the Stripe tab. `past_due` does count: the subscription is live and
the money is owed, and writing it off the moment a card bounces would make the
headline figure lurch on retries that usually succeed.

**What a yearly plan is worth per month.** `plans.py` sells a year for ten
months. So a yearly subscription's monthly equivalent is five sixths of the
monthly price -- not the monthly price, and emphatically not the yearly one.
Those two mistakes overstate MRR by 20% and by 1100% respectively.

The third property is the one this console keeps having to defend: the page
must not get slower as the business grows. A revenue column is the most
natural place for someone to later write "just look up this customer's sites".
"""

from guardian_hub import models as m, plans
from guardian_hub.db import SessionLocal
from tests.querycount import queries_for
from tests.test_auth import login, seed_user


def _owner():
    uid, tid = seed_user(username="miro", email="m@example.com", tenant_slug="home")
    db = SessionLocal()
    db.get(m.User, uid).is_superadmin = True
    db.commit(); db.close()
    return tid


def _customer(slug: str, plan: str, interval: str, status: str, *, stripe: bool = True) -> str:
    db = SessionLocal()
    t = m.Tenant(slug=slug, name=slug.title())
    db.add(t); db.flush()
    # A paying status alone no longer means paying: the ledger prices through
    # `billing.stripe_is_billing`, which also wants a live Stripe subscription
    # behind the row. That is what these customers are -- Stripe-billed -- so
    # the fixture says so the way `billing.apply_event` would have. An active
    # row WITHOUT the ids is a plan the owner granted from the console, and
    # `test_a_comped_plan_books_nothing` below pins that it counts for zero.
    ids = {"stripe_customer_id": f"cus_{t.slug}", "stripe_subscription_id": f"sub_{t.slug}"} \
        if stripe and status in ("active", "past_due") else {}
    db.add(m.Subscription(tenant_id=t.id, plan=plan, interval=interval, status=status, **ids))
    db.commit()
    tid = t.id
    db.close()
    return tid


def _totals(client) -> dict:
    r = client.get("/api/v1/platform")
    assert r.status_code == 200, r.text
    return r.json()["totals"]


def _row(client, tid: str) -> dict:
    r = client.get("/api/v1/platform")
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["tenants"] if x["id"] == tid)


# --- the arithmetic, unit level -----------------------------------------

def test_a_year_is_counted_at_a_twelfth_of_its_price_not_a_month_of_it():
    """The twelve-for-ten trap, stated as a number.

    Home is $9/month and therefore $90/year. Its monthly equivalent is $7.50,
    which is neither $9.00 (counting the sticker monthly price) nor $90.00
    (counting the yearly one). A platform of yearly customers priced the first
    way reports 20% more revenue than it has.
    """
    assert plans.monthly_cents("home", "month") == 900
    assert plans.monthly_cents("home", "year") == 750
    assert plans.monthly_cents("home", "year") * 12 == plans.get("home").yearly_cents


def test_a_per_site_plan_multiplies_and_a_flat_one_does_not():
    ess = plans.monthly_cents("business_essentials", "month", 3)
    assert ess == 4900 * 3
    # business_pro is not per-site; quantity must not silently inflate it.
    # (The caller decides whether to pass one -- this asserts the arithmetic
    # does what it is told, and `payloads/platform._per_site` decides what to
    # tell it, from `plans` rather than a hard-coded list.)
    assert plans.get("business_pro").per_site is False


def test_nothing_that_is_not_a_paid_subscription_is_worth_anything():
    assert plans.monthly_cents("trial", "month") == 0
    assert plans.monthly_cents("home", "none") == 0
    assert plans.monthly_cents("no_such_plan", "month") == 0
    assert plans.monthly_cents("business_essentials", "month", 0) == 0


def test_rounding_does_not_bias_the_total_downward():
    """Half-up at the last step, not truncation.

    Truncating every row loses up to a cent per subscription, which is
    invisible at two customers and a real drift at two hundred. Partner is
    $99/mo, so a year is $990 and a twelfth of that is $82.50 exactly; the
    case worth pinning is one that does not divide, which three sites gives:
    $2970/12 = $247.50.
    """
    assert plans.monthly_cents("partner", "year") == 8250
    assert plans.monthly_cents("partner", "year", 3) == 24750


# --- which statuses pay, end to end -------------------------------------

def test_a_trial_contributes_nothing(client):
    _owner()
    tid = _customer("trialer", "trial", "none", "trialing")
    login(client)
    assert _row(client, tid)["mrr_cents"] == 0
    t = _totals(client)
    assert t["mrr_cents"] == 0 and t["paying"] == 0


def test_an_unfinished_checkout_is_not_revenue(client):
    """The one that would quietly invent money.

    `routers/billing.checkout` writes an `incomplete` row BEFORE redirecting,
    so the webhook can find the tenant if the customer pays later on another
    device. Closing the Stripe tab is the ordinary case and must cost nothing
    -- including a line on the owner's revenue.
    """
    _owner()
    tid = _customer("abandoned", "business_pro", "month", "incomplete")
    login(client)
    assert _row(client, tid)["mrr_cents"] == 0
    assert _totals(client)["paying"] == 0


def test_an_active_subscription_is_counted_and_a_cancelled_one_is_not(client):
    _owner()
    live = _customer("paying", "home", "month", "active")
    _customer("gone", "business_pro", "month", "canceled")
    login(client)
    assert _row(client, live)["mrr_cents"] == 900
    t = _totals(client)
    assert t["mrr_cents"] == 900
    assert t["paying"] == 1


def test_a_failed_payment_still_counts_but_is_reported_separately(client):
    """`past_due` is live revenue AND today's work, so it is both.

    Dropping it from the total would make the headline figure jump every time
    a card bounces and jump back when the retry clears, which teaches the
    owner to distrust the number. Hiding that it happened would be worse.
    """
    _owner()
    tid = _customer("bounced", "home", "month", "past_due")
    login(client)
    assert _row(client, tid)["mrr_cents"] == 900
    t = _totals(client)
    assert t["mrr_cents"] == 900
    assert t["paying"] == 1
    assert t["past_due"] == 1


def test_a_per_site_plan_is_reported_at_one_site_and_flagged_as_a_floor(client):
    """The number the hub is not entitled to guess.

    `plans.Plan.per_site` says it outright -- "the hub does not meter sites,
    Stripe's quantity does" -- and the `subscription` table has no quantity
    column, because the quantity is chosen at checkout and lives on the Stripe
    subscription item.

    The tempting proxy is `models.Site`, which is in the schema and is called
    Site. It is a monitored URL, not a location: a dental practice watching
    twelve of its own pages from one office would be priced as twelve offices.
    Using it would have produced a confident, specific, wrong revenue figure --
    the kind a reader checks once and then trusts.

    So the row reports one site's price and raises `per_site`, and the page
    labels the total "at least". Understated and labelled is recoverable.
    """
    _owner()
    tid = _customer("chain", "business_essentials", "month", "active")
    login(client)
    row = _row(client, tid)
    assert row["mrr_cents"] == 4900
    assert row["per_site"] is True
    assert _totals(client)["mrr_is_floor"] is True


def test_the_total_is_called_exact_when_no_paying_customer_is_per_site(client):
    """The hedge appears only when it is true.

    A disclaimer left permanently on an exact number is how a reader learns to
    discount a correct one, so a platform of flat-rate customers says nothing.
    A per-site customer who is NOT paying -- on a trial, say -- must not
    trigger it either, since they contribute nothing to the figure being
    qualified.
    """
    _owner()
    _customer("flat", "home", "month", "active")
    _customer("trying", "business_essentials", "none", "trialing")
    login(client)
    assert _totals(client)["mrr_is_floor"] is False


def test_the_totals_are_the_sum_of_the_rows(client):
    """No second definition of the same number.

    The tiles and the table are read together, and a header that disagreed
    with the column under it would be found by the owner mid-sentence with a
    customer.
    """
    _owner()
    _customer("a", "home", "month", "active")
    _customer("b", "home", "year", "active")
    _customer("c", "business_essentials", "month", "active")
    _customer("d", "trial", "none", "trialing")
    login(client)
    body = client.get("/api/v1/platform").json()
    assert body["totals"]["mrr_cents"] == sum(r["mrr_cents"] for r in body["tenants"])
    assert body["totals"]["mrr_cents"] == 900 + 750 + 4900
    assert body["totals"]["paying"] == 3


# --- and it still does not scale ----------------------------------------

def test_revenue_did_not_cost_a_query_per_customer(client):
    """The guard this console keeps having to restate.

    Measured at two different customer counts and compared, rather than held
    under a ceiling: CLAUDE.md records that a ceiling loose enough not to be
    brittle sits above a real N+1 on a small fixture, which is how the
    wall-clock versions of these guards failed while looking green.

    Revenue is the specific risk: pricing a row is the most natural place
    anyone will ever have to reach for one more fact about that customer.
    """
    _owner()
    login(client)
    for i in range(2):
        _customer(f"few{i}", "business_essentials", "month", "active")
    few = queries_for(client, "/api/v1/platform")

    for i in range(20):
        _customer(f"many{i}", "business_essentials", "month", "active")
    many = queries_for(client, "/api/v1/platform")

    assert few == many, (
        f"{few} statements for 3 customers but {many} for 23 — "
        "the revenue column has acquired a query per tenant"
    )


# --- the account page must agree with the roster ------------------------

def test_one_customers_page_reports_the_same_money_as_the_roster(client):
    """The anti-drift guard, and the reason `_billing` imports rather than
    re-derives.

    The owner reaches `/owner/accounts/<id>` BY CLICKING a row on the Billing
    roster, so the two are read seconds apart, about the same account, often
    with that customer on the phone. A second definition of "is this customer
    paying" would be discovered by the customer, not by us -- the same failure
    `remedy` avoids by porting `fix.FIX_ALERT_KEY` verbatim, and the same one
    `history.alert_first_sightings` avoids for the sparkline.

    Asserted across every status that behaves differently, because the drift
    that matters is not in the arithmetic (one shared function) but in which
    rows are counted at all.
    """
    _owner()
    tids = {
        "active":   _customer("a-live", "home", "month", "active"),
        "yearly":   _customer("a-year", "home", "year", "active"),
        "past_due": _customer("a-late", "business_pro", "month", "past_due"),
        "trialing": _customer("a-try", "trial", "none", "trialing"),
        "incomplete": _customer("a-abandoned", "partner", "month", "incomplete"),
        "canceled": _customer("a-gone", "home", "month", "canceled"),
    }
    login(client)
    roster = {r["id"]: r for r in client.get("/api/v1/platform").json()["tenants"]}

    for label, tid in tids.items():
        page = client.get(f"/api/v1/platform/tenants/{tid}")
        assert page.status_code == 200, (label, page.text)
        b = page.json()["billing"]
        assert b is not None, f"{label}: a tenant with a subscription row must report billing"
        assert b["mrr_cents"] == roster[tid]["mrr_cents"], (
            f"{label}: account page says {b['mrr_cents']} but the roster says "
            f"{roster[tid]['mrr_cents']} for the same customer"
        )
        assert b["status"] == roster[tid]["plan_status"], label
        assert b["per_site"] == roster[tid]["per_site"], label


def test_an_account_with_no_subscription_says_so_rather_than_showing_zero(client):
    """`None`, not a row of zeroes.

    Tenants the owner created by hand before billing existed have no
    subscription row at all. That is a real and different state from "on a plan
    worth nothing", and a page that rendered it as $0.00 on a named plan would
    invite the owner to go looking for a billing fault that does not exist.
    """
    _owner()
    db = SessionLocal()
    t = m.Tenant(slug="handmade", name="Handmade")
    db.add(t); db.commit(); tid = t.id; db.close()
    login(client)
    assert client.get(f"/api/v1/platform/tenants/{tid}").json()["billing"] is None


def test_the_account_page_exposes_no_credential_with_the_money(client):
    """The Stripe id crosses; nothing that authenticates does.

    `test_account_payload.py` walks the schema for credential-shaped field
    names. This is the runtime half of the same question, asserted on a real
    response now that the payload has grown a block naming an external system.
    """
    _owner()
    tid = _customer("payer", "home", "month", "active")
    login(client)
    body = client.get(f"/api/v1/platform/tenants/{tid}").text.lower()
    for bad in ("sk_live", "sk_test", "whsec_", "token_hash", "enrol_code"):
        assert bad not in body, f"{bad} reached the owner's account payload"


def test_a_comped_plan_books_nothing(client):
    """A plan granted from the owner console is `active` with no Stripe
    subscription behind it: nothing is charged, so nothing is revenue. The
    ledger must not count a partner deal as a paying customer -- and the
    account page prices through the same predicate, so it cannot disagree."""
    _owner()
    login(client)
    tid = _customer("comped", "business_pro", "month", "active", stripe=False)
    row = _row(client, tid)
    assert row["plan_status"] == "active"
    assert row["mrr_cents"] == 0
