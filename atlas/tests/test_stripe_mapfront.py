"""Mapfront's Stripe webhook (2026-09-30): signed, idempotent, read-and-record
only. Payloads are fixtures signed here with a test secret; no Stripe call
is made, and none could be -- the module has no key."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from atlas.api import stripe_mapfront
from atlas.modules.agency import _store, escalate, payments
from tests.test_phone import client  # noqa: F401  (fixture)

SECRET = "whsec_test_" + "x" * 32
LEAD = {"id": 7, "name": "Joe's Plumbing"}


def _sign(body: bytes, secret: str = SECRET, t: int | None = None) -> str:
    stamp = str(t if t is not None else int(time.time()))
    sig = hmac.new(secret.encode(), stamp.encode() + b"." + body,
                   hashlib.sha256).hexdigest()
    return f"t={stamp},v1={sig}"


def _event(kind: str, obj: dict, event_id: str = "evt_1") -> bytes:
    return json.dumps({"id": event_id, "type": kind,
                       "data": {"object": obj}}).encode()


CHECKOUT = {"id": "cs_1", "payment_status": "paid", "amount_total": 50050,
            "currency": "usd", "client_reference_id": "lead_7",
            "payment_intent": "pi_1", "customer_details": {"email": "joe@x.com"}}
INVOICE = {"id": "in_1", "amount_paid": 30000, "currency": "usd",
           "customer_email": "joe@x.com", "payment_intent": "pi_2", "charge": "ch_2"}


def _returns(value):
    async def fake(*_a, **_kw):
        return value
    return fake


@pytest.fixture
def db(monkeypatch):
    rec: dict = {"claimed": set(), "payments": [], "deals": [], "status": [],
                 "deal_status": [], "notified": [], "emergencies": [],
                 "released": []}

    async def claim(event_id, kind):
        if event_id in rec["claimed"]:
            return False
        rec["claimed"].add(event_id)
        return True

    async def release(event_id):
        rec["released"].append(event_id)
        rec["claimed"].discard(event_id)

    async def get_lead(lid):
        return dict(LEAD) if lid == 7 else None

    async def paid_deal(lead_id, amount_minor, currency, note):
        rec["deals"].append((lead_id, amount_minor, currency))
        return 11

    async def payment(**row):
        rec["payments"].append(row)
        return 21

    async def set_status(pid, status, refunded=None):
        rec["status"].append((pid, status, refunded))

    async def set_deal(did, status):
        rec["deal_status"].append((did, status))

    async def notify(msg):
        rec["notified"].append(msg)

    async def emergency(business, kind, body):
        rec["emergencies"].append((business, kind, body))
        return "rang the owner"

    monkeypatch.setattr(_store, "claim_stripe_event", claim)
    monkeypatch.setattr(_store, "release_stripe_event", release)
    monkeypatch.setattr(_store, "get_lead", get_lead)
    monkeypatch.setattr(_store, "lead_id_for_email", _returns(None))
    monkeypatch.setattr(_store, "insert_paid_deal", paid_deal)
    monkeypatch.setattr(_store, "insert_payment", payment)
    async def payment_for(pi, charge):
        for row in rec["payments"]:
            if (pi and row.get("payment_intent") == pi) or (
                    charge and row.get("charge") == charge):
                return {"id": 21, "deal_id": 11, "lead_name": "Joe's Plumbing"}
        return rec.get("existing")
    monkeypatch.setattr(_store, "payment_for", payment_for)
    monkeypatch.setattr(_store, "set_payment_status", set_status)
    monkeypatch.setattr(_store, "set_deal_status", set_deal)
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    monkeypatch.setattr(escalate, "emergency", emergency)
    monkeypatch.setenv("MAPFRONT_STRIPE_WEBHOOK_SECRET", SECRET)
    return rec


def _post(client, body: bytes, header: str | None = None):  # noqa: F811
    return client.post(stripe_mapfront.PATH, content=body, headers={
        "Stripe-Signature": header if header is not None else _sign(body),
        "Content-Type": "application/json"})


# ------------------------------------------------------------ signature

def test_the_signature_scheme():
    body = b'{"id":"evt"}'
    assert stripe_mapfront.verify(body, _sign(body), SECRET)
    assert not stripe_mapfront.verify(body + b" ", _sign(body), SECRET)
    assert not stripe_mapfront.verify(body, _sign(body, "whsec_other"), SECRET)
    old = int(time.time()) - 3600
    assert not stripe_mapfront.verify(body, _sign(body, t=old), SECRET)
    assert not stripe_mapfront.verify(body, "garbage", SECRET)
    assert not stripe_mapfront.verify(body, _sign(body), "")
    both = f"{_sign(body, 'whsec_rotated')},v1={_sign(body).split('v1=')[1]}"
    assert stripe_mapfront.verify(body, both, SECRET)       # secret rotation


def test_unset_secret_is_refused(client, db, monkeypatch):  # noqa: F811
    monkeypatch.delenv("MAPFRONT_STRIPE_WEBHOOK_SECRET")
    body = _event("invoice.paid", INVOICE)
    assert _post(client, body).status_code == 503
    assert db["payments"] == []


def test_a_bad_signature_is_refused_and_nothing_logged(client, db, caplog):  # noqa: F811
    body = _event("invoice.paid", INVOICE)
    r = _post(client, body, _sign(body, "whsec_wrong"))
    assert r.status_code == 401 and db["payments"] == []
    assert _post(client, body, "").status_code == 400
    logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert SECRET not in logged and "joe@x.com" not in logged


def test_the_route_is_public_exactly(client):  # noqa: F811
    from atlas.api import main
    assert main._is_public("/webhooks/stripe-mapfront")
    assert "/webhooks/stripe-mapfront" in main.PUBLIC_EXACT


# ------------------------------------------------------------ payments

def test_a_checkout_payment_is_recorded_exactly_and_ends_first_sale(
        client, db):  # noqa: F811
    r = _post(client, _event("checkout.session.completed", CHECKOUT))
    assert r.status_code == 200 and r.json()["recorded"]
    assert db["deals"] == [(7, 50050, "usd")]
    [row] = db["payments"]
    assert row["amount_minor"] == 50050 and row["matched_by"] == "reference"
    [msg] = db["notified"]
    assert msg.title.startswith("Mapfront: Joe's Plumbing paid $500.50 (Stripe, read ")
    assert msg.topic == "agency"


def test_the_same_event_twice_records_once(client, db):  # noqa: F811
    body = _event("checkout.session.completed", CHECKOUT)
    assert _post(client, body).json()["recorded"]
    again = _post(client, body).json()
    assert again["duplicate"] and not again["recorded"]
    assert len(db["payments"]) == 1 and len(db["deals"]) == 1


def test_one_payment_under_two_events_is_recorded_once(client, db):  # noqa: F811
    """A Payment Link with post-payment invoices sends both events."""
    _post(client, _event("checkout.session.completed", {**CHECKOUT,
                                                        "payment_intent": "pi_2"}))
    again = _post(client, _event("invoice.paid", INVOICE, "evt_inv2")).json()
    assert again["recorded"] is True                 # the event was handled...
    assert len(db["payments"]) == 1 and len(db["deals"]) == 1   # ...once
    assert len(db["notified"]) == 1


def test_an_invoice_matches_by_email(client, db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(_store, "lead_id_for_email", _returns(7))
    _post(client, _event("invoice.paid", INVOICE, "evt_inv"))
    assert db["payments"][0]["matched_by"] == "email"
    assert db["deals"] == [(7, 30000, "usd")]


def test_an_unmatched_payment_is_recorded_without_a_deal(client, db):  # noqa: F811
    _post(client, _event("invoice.paid", {**INVOICE, "customer_email": "who@x.com"},
                         "evt_un"))
    [row] = db["payments"]
    assert row["lead_id"] is None and row["matched_by"] == "none"
    assert db["deals"] == []
    assert "an unmatched customer paid $300" in db["notified"][0].title


def test_an_unpaid_checkout_records_nothing(client, db):  # noqa: F811
    _post(client, _event("checkout.session.completed",
                         {**CHECKOUT, "payment_status": "unpaid"}, "evt_u"))
    assert db["payments"] == [] and db["notified"] == []


def test_other_event_types_are_acknowledged_and_ignored(client, db):  # noqa: F811
    r = _post(client, _event("customer.created", {"id": "cus_1"}, "evt_c"))
    assert r.status_code == 200 and not r.json()["recorded"]
    assert db["claimed"] == set()


# ------------------------------------------------------ refunds, disputes

def test_a_full_refund_takes_the_deal_off_paid_and_rings_owner(client, db):  # noqa: F811
    db["existing"] = {"id": 21, "deal_id": 11, "lead_name": "Joe's Plumbing"}
    _post(client, _event("charge.refunded", {
        "id": "ch_2", "payment_intent": "pi_2", "amount": 30000,
        "amount_refunded": 30000, "currency": "usd"}, "evt_r"))
    assert db["status"] == [(21, "refunded", 30000)]
    assert db["deal_status"] == [(11, "refunded")]
    assert db["emergencies"][0][1] == "a refund or a payment dispute"


def test_a_dispute_is_an_emergency(client, db):  # noqa: F811
    db["existing"] = {"id": 21, "deal_id": 11, "lead_name": "Joe's Plumbing"}
    _post(client, _event("charge.dispute.created", {
        "id": "dp_1", "charge": "ch_2", "payment_intent": "pi_2", "amount": 30000,
        "currency": "usd", "reason": "fraudulent"}, "evt_d"))
    assert db["status"] == [(21, "disputed", None)]
    assert db["deal_status"] == [(11, "disputed")]
    business, _kind, body = db["emergencies"][0]
    assert business == "Joe's Plumbing" and "$300" in body and "fraudulent" in body


def test_a_failure_releases_the_claim_for_stripes_retry(client, db, monkeypatch):  # noqa: F811
    async def boom(**_row):
        raise RuntimeError("db down")
    monkeypatch.setattr(_store, "insert_payment", boom)
    r = _post(client, _event("invoice.paid", INVOICE, "evt_f"))
    assert r.status_code == 500 and db["released"] == ["evt_f"]


def test_money_is_shown_exactly():
    assert payments.money(50000, "usd") == "$500"
    assert payments.money(50050, "usd") == "$500.50"
    assert payments.money(1200, "eur") == "12 EUR"


def test_the_agreement_link_names_the_lead(monkeypatch):
    from atlas.modules.agency import replies
    monkeypatch.setenv("AGENCY_PAYMENT_LINKS", "500=https://buy.stripe.com/abc")
    assert replies.payment_url(7, 500) == (
        "https://buy.stripe.com/abc?client_reference_id=lead_7")
    assert replies.payment_url(7, 300) is None


def test_nothing_here_can_move_money():
    import inspect
    for module in (payments, stripe_mapfront):
        source = inspect.getsource(module)
        assert "api.stripe.com" not in source and "httpx" not in source
