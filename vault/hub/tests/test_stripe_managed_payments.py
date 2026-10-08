"""What the Checkout Session carries when Stripe is the merchant of record.

Under Managed Payments, Stripe is liable for sales tax and VAT and handles
fraud, disputes, invoicing and receipts. On our side it is one parameter --
but it is a parameter that makes Stripe take over parts of the session, and
Stripe REJECTS a Managed Payments session that also sets any of the fields it
now owns. A rejected session is not a degraded checkout, it is no checkout at
all, so the forbidden list is worth a test rather than a comment.
"""

import pytest

from guardian_hub import stripe_api
from guardian_hub.config import settings

# Every Checkout Session parameter Stripe forbids in subscription mode under
# Managed Payments. Taken from the "Remove from code" list the dashboard shows
# on the Managed Payments setup page, which is authoritative and did not match
# the docs site: the dashboard additionally names `payment_method_collection`,
# `saved_payment_method_options` and the two `customer_update` fields, and does
# not name `adaptive_pricing` or `invoice_creation`.
#
# Those last two are kept anyway. Being stricter than Stripe costs nothing here
# -- neither is a parameter this hub would want once Stripe owns the invoice
# and the payment methods -- and the two sources disagreeing is itself a reason
# not to trust either one to be complete.
FORBIDDEN = (
    "adaptive_pricing",
    "automatic_tax",
    "tax_id_collection",
    "payment_method_collection",
    "payment_method_configuration",
    "payment_method_options",
    "payment_method_types",
    "saved_payment_method_options",
    "customer_update",
    "shipping_address_collection",
    "shipping_options",
    "invoice_creation",
)
FORBIDDEN_SUBSCRIPTION_DATA = (
    "default_tax_rates",
    "application_fee_percent",
    "on_behalf_of",
    "transfer_data",
    "invoice_settings",
)


@pytest.fixture
def sent(monkeypatch):
    """Capture the body `create_checkout_session` would post to Stripe."""
    box: dict = {}

    def fake_request(method, path, data=None, *, idempotency_key="", api_version=""):
        box["method"], box["path"], box["data"] = method, path, data
        box["api_version"], box["idempotency_key"] = api_version, idempotency_key
        return {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"}

    monkeypatch.setattr(stripe_api, "_request", fake_request)
    return box


def _checkout(**overrides):
    kw = dict(
        price_id="price_123", quantity=3, tenant_id="t_1",
        customer_id="", customer_email="dana@northwind.example",
        success_url="https://my.guarder.io/ok", cancel_url="https://my.guarder.io/no",
    )
    kw.update(overrides)
    return stripe_api.create_checkout_session(**kw)


def test_the_flag_is_absent_until_someone_turns_it_on(sent, monkeypatch):
    # The default matters more than it looks: sending this to an account that
    # has not enabled Managed Payments and accepted its terms is an error from
    # Stripe, so a hub that shipped with it on would have no checkout at all.
    monkeypatch.setattr(settings, "stripe_managed_payments", False)
    _checkout()
    assert "managed_payments" not in sent["data"]


def test_turning_it_on_asks_stripe_to_be_the_merchant_of_record(sent, monkeypatch):
    monkeypatch.setattr(settings, "stripe_managed_payments", True)
    _checkout()
    assert sent["data"]["managed_payments"] == {"enabled": True}
    # Form-encoded, this is what actually goes over the wire.
    assert stripe_api._flatten(sent["data"])["managed_payments[enabled]"] == "true"


def test_the_session_never_sets_a_parameter_managed_payments_forbids(sent, monkeypatch):
    monkeypatch.setattr(settings, "stripe_managed_payments", True)
    _checkout()
    data = sent["data"]
    clashes = [k for k in FORBIDDEN if k in data]
    assert clashes == [], f"Managed Payments rejects a session that sets: {clashes}"
    sub = data.get("subscription_data") or {}
    sub_clashes = [k for k in FORBIDDEN_SUBSCRIPTION_DATA if k in sub]
    assert sub_clashes == [], f"forbidden under subscription_data: {sub_clashes}"


def test_the_tenant_still_rides_on_the_subscription(sent, monkeypatch):
    # `subscription_data.metadata` is the one field in that neighbourhood that
    # Managed Payments leaves alone, and everything depends on it:
    # `billing.apply_event` reads `tenant_id` from it to find the account on
    # later `customer.subscription.*` events, which carry no
    # `client_reference_id`. Without it a paid subscription belongs to nobody.
    monkeypatch.setattr(settings, "stripe_managed_payments", True)
    _checkout()
    assert sent["data"]["subscription_data"]["metadata"]["tenant_id"] == "t_1"
    assert sent["data"]["client_reference_id"] == "t_1"
    assert sent["data"]["mode"] == "subscription"


def test_the_flag_travels_under_an_api_version_that_knows_it(sent, monkeypatch):
    """The failure this rules out is silent, which is why it is worth a test.

    `managed_payments` does not exist before 2025-03-31.basil, and the hub is
    otherwise pinned to an older version. Stripe does not have to reject an
    unknown parameter -- and a silently dropped one means an ordinary session:
    we are the merchant of record, we are liable for the sales tax, and the
    dashboard still says Managed Payments is on. Nothing would look broken.
    """
    monkeypatch.setattr(settings, "stripe_managed_payments", True)
    _checkout()
    assert sent["api_version"] == stripe_api.CHECKOUT_API_VERSION


def test_a_session_is_created_under_basil_whether_or_not_the_flag_is_on(sent, monkeypatch):
    """This used to assert the opposite, and the opposite was wrong.

    The old belief: only a session that SENDS `managed_payments` needs the
    newer API version, so with the flag off the call could stay on the hub's
    general pin. Stripe disagrees, at the account level: once an account has
    accepted Managed Payments, it refuses ANY Checkout Session created under a
    pre-basil version, param or no param. Reproduced 2026-09-21 against the
    sandbox account with two otherwise identical requests carrying no
    `managed_payments` field at all -- `2024-06-20` answered "Managed Payments
    is not supported on API version 2024-06-20", `2025-03-31.basil` answered
    with a session.

    Both Vault accounts have accepted it, so with the flag off every
    Upgrade click was a 502 -- and `billing-check` still reported all clear,
    because it verified prices and never made a session. Only the version of
    this one call moves; the rest of the integration keeps its pin, for the
    reason the module comment gives.
    """
    monkeypatch.setattr(settings, "stripe_managed_payments", False)
    _checkout()
    assert sent["api_version"] == stripe_api.CHECKOUT_API_VERSION
    assert "managed_payments" not in sent["data"]


# ---------------------------------------------------------------------------
# The idempotency key must describe the request it protects
# ---------------------------------------------------------------------------
#
# The route builds a key from tenant, plan, interval and the UTC hour, so a
# double-click yields one session rather than two. Reproduced 2026-09-21: the
# key carried nothing about the request itself, so after one attempt failed
# (wrong API version) a retry with corrected config -- same key, different
# body -- was refused by Stripe: "Keys for idempotent requests can only be
# used with the same parameters they were first used with." A customer who
# met a transient error could not retry the same plan for up to an hour.
#
# The fix is for the key to carry a digest of what is actually sent, so an
# identical retry is still deduplicated and a CHANGED retry gets its own key.

def test_two_identical_requests_share_one_idempotency_key(sent, monkeypatch):
    monkeypatch.setattr(settings, "stripe_managed_payments", True)
    _checkout(idempotency_key="co:t_1:home:month:2026092101")
    first = sent["idempotency_key"]
    _checkout(idempotency_key="co:t_1:home:month:2026092101")
    assert sent["idempotency_key"] == first
    # The route's scope is still legible at the front, for the audit trail.
    assert first.startswith("co:t_1:home:month:2026092101")


def test_a_changed_request_under_the_same_scope_gets_a_different_key(sent, monkeypatch):
    monkeypatch.setattr(settings, "stripe_managed_payments", False)
    _checkout(idempotency_key="co:t_1:home:month:2026092101")
    without_flag = sent["idempotency_key"]
    monkeypatch.setattr(settings, "stripe_managed_payments", True)
    _checkout(idempotency_key="co:t_1:home:month:2026092101")
    with_flag = sent["idempotency_key"]
    assert with_flag != without_flag
    assert with_flag.startswith("co:t_1:home:month:2026092101")
    # ...and so does any other change to what goes over the wire.
    _checkout(idempotency_key="co:t_1:home:month:2026092101", customer_email="ben@northwind.example")
    assert sent["idempotency_key"] not in (with_flag, without_flag)


def test_no_scope_means_no_idempotency_key_at_all(sent, monkeypatch):
    # A caller that passes nothing wants no dedup, not a key made of nothing.
    monkeypatch.setattr(settings, "stripe_managed_payments", True)
    _checkout()
    assert sent["idempotency_key"] == ""
