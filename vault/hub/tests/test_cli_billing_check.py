"""`guardian billing-check`: does this hub's Stripe account actually carry a
correct price for every plan it offers to sell?

The command's whole value is that it fails loudly on paper instead of quietly
at a real customer's checkout, so what this file holds down is the failing
side: a wrong amount and a missing lookup key must each name the key, exit
non-zero, and still print the other seven rows.

Nothing here touches the network. The fake is installed one layer *below* the
function under test -- `stripe_api._request` rather than
`stripe_api.price_for_lookup_key` -- so the query that decides which price you
get is exercised too, which is the point of that function being shared with
checkout in the first place.
"""

import pytest

from guardian_hub import cli, plans, stripe_api
from guardian_hub.config import settings

# Four paid plans times two intervals. Pinned so that adding a plan without
# creating its Stripe prices is a failing test rather than a quietly shorter
# table.
EXPECTED_ROWS = 8


def _catalogue() -> dict[str, dict]:
    """The prices a correctly configured Stripe account holds, derived from
    `plans` rather than written out -- a hand-copied amount here could agree
    with a typo in the catalogue and the check would pass on both."""
    return {
        plan.lookup_key(interval): {
            "id": f"price_{plan.id}_{interval}",
            "unit_amount": plan.cents(interval),
            "currency": plans.CURRENCY,
            "recurring": {"interval": interval},
            "active": True,
        }
        for plan in plans.PAID_PLANS
        for interval in plans.INTERVALS
    }


class _Catalogue(dict):
    """The prices a test owns, plus what the fake Stripe saw and how it should
    answer the checkout probe. `reject_checkout` set to a message makes the
    probe's create call fail the way Stripe does."""
    calls: list
    reject_checkout: str = ""


@pytest.fixture()
def prices(monkeypatch):
    """Configure billing and serve `/v1/prices` out of a dict the test owns:
    mutate or delete an entry to describe a misconfigured Stripe account."""
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_not_a_real_key")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_not_a_real_secret")
    rows = _Catalogue(_catalogue())
    rows.calls = []

    def fake_request(method, path, data=None, *, idempotency_key="", api_version=""):
        rows.calls.append((method, path, data or {}, idempotency_key, api_version))
        if (method, path) == ("GET", "/v1/prices"):
            key = ((data or {}).get("lookup_keys") or [""])[0]
            row = rows.get(key)
            return {"data": [row] if row else []}
        if (method, path) == ("POST", "/v1/checkout/sessions"):
            if rows.reject_checkout:
                raise stripe_api.StripeError(rows.reject_checkout)
            return {"id": "cs_test_probe", "url": "https://checkout.stripe.com/c/pay/cs_test_probe"}
        if method == "POST" and path == "/v1/checkout/sessions/cs_test_probe/expire":
            return {"id": "cs_test_probe", "status": "expired"}
        raise AssertionError(f"unexpected Stripe call {method} {path}")

    monkeypatch.setattr(stripe_api, "_request", fake_request)
    return rows


def _run(capsys) -> tuple[int, str]:
    with pytest.raises(SystemExit) as exc:
        cli.main(["billing-check"])
    return int(exc.value.code or 0), capsys.readouterr().out


def _row(out: str, lookup_key: str) -> str:
    return next(line for line in out.splitlines() if line.startswith(lookup_key + " "))


def test_a_fully_configured_account_passes_every_plan_and_interval(prices, capsys):
    code, out = _run(capsys)
    assert code == 0
    assert len(prices) == EXPECTED_ROWS
    for key in prices:
        assert _row(out, key).endswith("OK")
    assert len([ln for ln in out.splitlines() if ln.endswith("OK")]) == EXPECTED_ROWS
    # The per-site column is what tells the owner which prices Stripe must
    # charge by quantity; Home is not one of them and Business Essentials is.
    assert "  yes  " in _row(out, "guardian_business_essentials_month")
    assert "  no  " in _row(out, "guardian_home_month")


def test_a_price_created_at_the_wrong_amount_is_named_and_fails(prices, capsys):
    prices["guardian_business_pro_month"]["unit_amount"] = 12900

    code, out = _run(capsys)

    assert code != 0
    row = _row(out, "guardian_business_pro_month")
    assert "$129.00" in row and "$149.00" in row
    # A failure in the middle of the table must not truncate it: the owner
    # should fix every wrong price in one trip to the dashboard.
    assert len([ln for ln in out.splitlines() if ln.endswith("OK")]) == EXPECTED_ROWS - 1


def test_a_lookup_key_that_does_not_exist_in_stripe_fails(prices, capsys):
    del prices["guardian_home_year"]

    code, out = _run(capsys)

    assert code != 0
    assert "no active Stripe price" in _row(out, "guardian_home_year")
    assert _row(out, "guardian_home_month").endswith("OK")


def test_every_way_one_price_is_wrong_is_reported_in_the_same_run(prices, capsys):
    wrong = prices["guardian_partner_year"]
    wrong["unit_amount"] = 1
    wrong["currency"] = "eur"
    wrong["recurring"] = {"interval": "month"}

    code, out = _run(capsys)

    assert code != 0
    row = _row(out, "guardian_partner_year")
    assert "$0.01" in row and "eur" in row and "recurring interval is month" in row


def _no_stripe_calls(monkeypatch):
    def never(*a, **kw):
        raise AssertionError("must not call Stripe with no secret key")
    monkeypatch.setattr(stripe_api, "_request", never)


def test_an_unconfigured_hub_names_the_env_var_instead_of_raising(monkeypatch, capsys):
    """Offering paid plans with no way to reach Stripe is a real fault."""
    monkeypatch.setattr(settings, "stripe_secret_key", "")
    monkeypatch.setattr(settings, "billing_mode", "plans")
    _no_stripe_calls(monkeypatch)

    code, out = _run(capsys)

    assert code != 0
    assert "GUARDIAN_STRIPE_SECRET_KEY" in out


def test_a_trial_only_hub_with_no_stripe_account_is_not_a_failure(monkeypatch, capsys):
    """The shipped default. `trial_only` refuses checkout before it ever looks
    up a price, so a hub with no Stripe account at all is doing exactly what
    it was configured to do -- reporting it as broken would train the owner to
    ignore this command."""
    monkeypatch.setattr(settings, "stripe_secret_key", "")
    monkeypatch.setattr(settings, "billing_mode", "trial_only")
    _no_stripe_calls(monkeypatch)

    code, out = _run(capsys)

    assert code == 0
    assert "free trial only" in out
    assert "GUARDIAN_BILLING_MODE=plans" in out
    assert "GUARDIAN_STRIPE_SECRET_KEY" in out   # still says how to prepare


def test_a_trial_only_hub_still_checks_the_prices_when_it_has_a_key(prices, monkeypatch, capsys):
    """Preparation is the whole point: the prices have to be right before the
    mode is switched, and switching is a setting, not a deploy."""
    monkeypatch.setattr(settings, "billing_mode", "trial_only")

    code, out = _run(capsys)

    assert code == 0
    assert "guardian_home_month" in out
    assert "nothing below is in use yet" in out.replace("\n", " ")


def test_a_missing_webhook_secret_is_reported_without_failing_the_prices(prices, monkeypatch, capsys):
    """The prices are what decides the exit status. A customer can still pay
    with no signing secret configured -- their subscription just never turns
    active here -- so it is a warning beside the table, not a failed row."""
    monkeypatch.setattr(settings, "stripe_webhook_secret", "")

    code, out = _run(capsys)

    assert code == 0
    assert "GUARDIAN_STRIPE_WEBHOOK_SECRET" in out


# ---------------------------------------------------------------------------
# The prices being right is not the same as a customer being able to pay
# ---------------------------------------------------------------------------
#
# 2026-09-21: every price resolved, billing-check said "all clear", and every
# Upgrade click was a 502 -- the account had accepted Managed Payments and
# refused any Checkout Session under the API version the hub was pinning. The
# check verified the catalogue and never asked the question the owner was
# asking. So it now asks it: create a real session against the account, then
# expire it at once so nothing dangles. That is a write on the live account,
# but an inert one -- a session charges nothing until a customer completes
# it, and this one is expired before the command returns.

def _checkout_calls(prices):
    return [c for c in prices.calls if c[1].startswith("/v1/checkout/sessions")]


def test_a_passing_run_proves_a_session_can_be_created_and_expires_it(prices, capsys):
    code, out = _run(capsys)
    assert code == 0
    assert "Checkout session: OK" in out
    create, expire = _checkout_calls(prices)
    assert create[:2] == ("POST", "/v1/checkout/sessions")
    assert create[4] == stripe_api.CHECKOUT_API_VERSION, "the probe must use the version checkout uses"
    assert create[2]["metadata"]["probe"] == "billing-check"
    assert expire[:2] == ("POST", "/v1/checkout/sessions/cs_test_probe/expire")


def test_a_session_stripe_refuses_is_named_and_fails_the_run(prices, capsys):
    prices.reject_checkout = "Managed Payments is not supported on API version 2024-06-20."
    code, out = _run(capsys)
    assert code == 1, "a hub whose customers cannot reach checkout is misconfigured"
    line = next(ln for ln in out.splitlines() if ln.startswith("Checkout session:"))
    assert line.startswith("Checkout session: FAIL")
    assert "Managed Payments is not supported" in line
    # Nothing to expire: the create never returned a session.
    assert [c[1] for c in _checkout_calls(prices)] == ["/v1/checkout/sessions"]


def test_the_probe_is_skipped_when_a_price_is_already_broken(prices, capsys):
    # A probe against a catalogue with a missing price would fail for the
    # price's reason and bury the row that already says so.
    del prices["guardian_home_month"]
    code, out = _run(capsys)
    assert code == 1
    assert _checkout_calls(prices) == []
    assert "Checkout session: skipped" in out
