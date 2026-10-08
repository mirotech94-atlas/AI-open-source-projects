"""Billing behaviour: the trial lifecycle, the webhook's defences, and what a
non-paying account keeps.

The four things this file exists to hold down, in the order they would hurt:

1. **An unsigned or stale webhook is never applied.** The route is
   unauthenticated and internet-facing.
2. **A replayed webhook is applied once.** Stripe retries for days.
3. **A trial that ends pauses; it never charges and never deletes.**
4. **A failed payment does not stop the monitoring.** A watchdog that stops
   watching because a card expired is worse than useless.
"""

import datetime as dt
import json
import time

import pytest

from guardian_hub import billing, models as m, plans, stripe_api
from guardian_hub.config import settings as _settings


@pytest.fixture(autouse=True)
def _offer_the_paid_plans(monkeypatch):
    """This module exercises the Stripe path -- checkout, the portal, the
    webhook -- so it pins the mode that offers paid plans. The hub's shipped
    default is "trial_only", where those routes answer 409 by design; that is
    `test_billing_trial_only.py`'s subject, not this one's.
    """
    monkeypatch.setattr(_settings, "billing_mode", "plans")

from guardian_hub.config import settings
from guardian_hub.db import SessionLocal
from tests.test_auth import login, seed_user

WHSEC = "whsec_test_only_never_a_real_secret"


@pytest.fixture()
def signed(monkeypatch):
    """Configure a webhook secret for the duration of one test and hand back
    a `(payload_bytes, header)` builder for it."""
    monkeypatch.setattr(settings, "stripe_webhook_secret", WHSEC)

    def build(event: dict, *, ts: int | None = None, secret: str = WHSEC):
        raw = json.dumps(event).encode()
        return raw, stripe_api.sign_payload(raw, secret, int(ts or time.time()))

    return build


def _post(client, raw: bytes, header: str | None):
    return client.post("/api/v1/billing/stripe/webhook", content=raw,
                       headers={"stripe-signature": header} if header else {})


def _sub_event(evt_id: str, *, tenant_id: str, status: str = "active",
               lookup_key: str = "guardian_home_month", created: int = 1_700_000_000,
               etype: str = "customer.subscription.created", period_end: int = 1_700_600_000,
               cancel_at_period_end: bool = False) -> dict:
    return {
        "id": evt_id, "type": etype, "created": created,
        "data": {"object": {
            "id": "sub_test_1", "object": "subscription", "customer": "cus_test_1",
            "status": status, "cancel_at_period_end": cancel_at_period_end,
            "current_period_end": period_end,
            "metadata": {"tenant_id": tenant_id},
            "items": {"data": [{"price": {"id": "price_1", "lookup_key": lookup_key,
                                          "recurring": {"interval": "month"}}}]},
        }},
    }


# --------------------------------------------------------------------------
# Webhook signature
# --------------------------------------------------------------------------

def test_webhook_rejects_a_missing_or_wrong_signature(client, signed):
    raw, header = signed({"id": "evt_1", "type": "ping", "created": int(time.time())})

    assert _post(client, raw, None).status_code == 400              # no header
    assert _post(client, raw, "v1=deadbeef").status_code == 400     # no timestamp
    assert _post(client, raw, "t=123").status_code == 400           # no signature
    assert _post(client, raw, "t=abc,v1=deadbeef").status_code == 400  # unparseable t
    # Right shape, wrong secret.
    _, wrong = signed({"id": "evt_1", "type": "ping", "created": int(time.time())},
                      secret="whsec_someone_elses")
    assert _post(client, raw, wrong).status_code == 400
    # Right signature over a DIFFERENT body: the MAC covers the raw bytes, so
    # swapping the body must invalidate it.
    assert _post(client, raw + b" ", header).status_code == 400
    # And the good one still works, so the rejections above are not incidental.
    assert _post(client, raw, header).status_code == 200


def test_webhook_rejects_a_stale_timestamp(client, signed):
    old = int(time.time()) - (settings.stripe_webhook_tolerance + 60)
    raw, header = signed({"id": "evt_old", "type": "ping", "created": old}, ts=old)
    r = _post(client, raw, header)
    assert r.status_code == 400 and "tolerance" in r.json()["detail"]
    # Nothing was recorded: a rejected call must not consume the event id, or
    # a genuine redelivery of it would be swallowed as a duplicate.
    assert SessionLocal().get(m.BillingEvent, "evt_old") is None


def test_webhook_rejects_everything_when_no_signing_secret_is_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "")
    raw = json.dumps({"id": "evt_x", "type": "ping", "created": int(time.time())}).encode()
    # Even a *correctly* signed call, because there is nothing to check it
    # against: "billing is not set up" must not mean "anyone may post".
    header = stripe_api.sign_payload(raw, WHSEC, int(time.time()))
    for h in (None, header):
        r = _post(client, raw, h)
        assert r.status_code == 400
        assert "not configured" in r.json()["detail"] or "missing" in r.json()["detail"]


def test_signature_verification_accepts_any_v1_during_a_secret_rotation():
    raw = b'{"id":"evt_rot"}'
    good = stripe_api.sign_payload(raw, WHSEC, 1000).split("v1=")[1]
    header = f"t=1000,v1=0000000000000000000000000000000000000000000000000000000000000000,v1={good}"
    assert stripe_api.verify_signature(raw, header, secret=WHSEC, now=1000) == {"id": "evt_rot"}


def test_signature_verification_rejects_a_non_json_body():
    raw = b"not json at all"
    header = stripe_api.sign_payload(raw, WHSEC, 1000)
    with pytest.raises(stripe_api.SignatureError):
        stripe_api.verify_signature(raw, header, secret=WHSEC, now=1000)


# --------------------------------------------------------------------------
# Webhook idempotency and ordering
# --------------------------------------------------------------------------

def test_a_replayed_webhook_is_applied_once_and_still_answers_200(client, signed):
    _uid, tid = seed_user(); login(client)
    client.post("/api/v1/billing/trial")

    raw, header = signed(_sub_event("evt_dup", tenant_id=tid))
    first = _post(client, raw, header)
    assert first.status_code == 200 and first.json()["handled"] is True

    for _ in range(3):
        again = _post(client, raw, header)
        # 200, not 409: any non-2xx has Stripe redelivering a handled event
        # for days.
        assert again.status_code == 200
        assert again.json()["duplicate"] is True and again.json()["handled"] is False

    db = SessionLocal()
    assert db.query(m.BillingEvent).filter_by(id="evt_dup").count() == 1
    sub = db.query(m.Subscription).filter_by(tenant_id=tid).one()
    assert sub.plan == "home" and sub.interval == "month" and sub.status == "active"


def test_an_out_of_order_event_does_not_resurrect_a_stale_status(client, signed):
    _uid, tid = seed_user(); login(client)
    client.post("/api/v1/billing/trial")

    # A cancellation at T+100 arrives first...
    raw, header = signed(_sub_event("evt_new", tenant_id=tid, status="canceled",
                                    created=1_700_000_100,
                                    etype="customer.subscription.updated"))
    assert _post(client, raw, header).json()["handled"] is True
    assert billing.get(SessionLocal(), tid).status == "canceled"

    # ...then the older `active` from T+0 turns up. It must be dropped.
    raw, header = signed(_sub_event("evt_old2", tenant_id=tid, status="active",
                                    created=1_700_000_000,
                                    etype="customer.subscription.updated"))
    r = _post(client, raw, header)
    assert r.status_code == 200 and r.json()["stale"] is True
    assert billing.get(SessionLocal(), tid).status == "canceled"
    # Still recorded as seen, so it is never reprocessed either.
    assert SessionLocal().get(m.BillingEvent, "evt_old2") is not None


def test_an_event_for_an_unknown_tenant_is_recorded_and_ignored(client, signed):
    seed_user(); login(client)
    raw, header = signed(_sub_event("evt_stranger", tenant_id="not-a-tenant"))
    r = _post(client, raw, header)
    assert r.status_code == 200 and r.json()["handled"] is False
    assert SessionLocal().get(m.BillingEvent, "evt_stranger") is not None
    # No subscription was invented from an unverifiable tenant id.
    assert SessionLocal().query(m.Subscription).count() == 0


def test_checkout_completed_then_subscription_created_walk_the_account_to_active(client, signed):
    _uid, tid = seed_user(); login(client)
    client.post("/api/v1/billing/trial")

    session_evt = {
        "id": "evt_co", "type": "checkout.session.completed", "created": 1_700_000_000,
        "data": {"object": {"id": "cs_1", "object": "checkout.session",
                            "customer": "cus_test_1", "subscription": "sub_test_1",
                            "client_reference_id": tid, "metadata": {"tenant_id": tid}}},
    }
    raw, header = signed(session_evt)
    assert _post(client, raw, header).json()["handled"] is True
    sub = billing.get(SessionLocal(), tid)
    assert sub.stripe_customer_id == "cus_test_1" and sub.stripe_subscription_id == "sub_test_1"
    # Not claimed active off the session alone: a 3DS/SEPA payment is still
    # unconfirmed at this point.
    assert sub.status == "trialing"

    raw, header = signed(_sub_event("evt_created", tenant_id=tid,
                                    lookup_key="guardian_partner_year",
                                    created=1_700_000_050))
    assert _post(client, raw, header).json()["handled"] is True
    sub = billing.get(SessionLocal(), tid)
    assert sub.status == "active" and sub.plan == "partner" and sub.interval == "year"
    # A paid account carries no leftover trial countdown.
    assert sub.trial_end is None
    assert sub.current_period_end is not None


def test_stripes_real_event_order_still_reaches_active(client, signed):
    """Stripe stamps `customer.subscription.created` BEFORE
    `checkout.session.completed`, and delivers them in either order.

    Measured against a live sandbox checkout on 2026-09-21:
    subscription.created created=1790013891, session.completed
    created=1790013892 -- the session is stamped a second LATER than the
    subscription it created. The listener delivered the session first.

    That order used to lose the sale. `apply_event`'s staleness guard
    compared `created` against a single watermark for every event type, so
    the session advanced it to ...892 and the subscription event, at ...891,
    was dropped as "predating the last applied event". The subscription
    event is the only one that carries status, plan, interval and the period
    end, so the account sat at `incomplete` for ever -- while every webhook
    answered 200 and Stripe's dashboard showed an active, paid subscription.

    The test above this one walks the same two events 50 seconds apart, which
    is why it never caught this: it modelled an order Stripe does not produce.
    """
    _uid, tid = seed_user(); login(client)
    client.post("/api/v1/billing/trial")

    session_evt = {
        "id": "evt_co_real", "type": "checkout.session.completed", "created": 1_790_013_892,
        "data": {"object": {"id": "cs_2", "object": "checkout.session",
                            "customer": "cus_test_1", "subscription": "sub_test_1",
                            "client_reference_id": tid, "metadata": {"tenant_id": tid}}},
    }
    raw, header = signed(session_evt)
    assert _post(client, raw, header).json()["handled"] is True

    raw, header = signed(_sub_event("evt_created_real", tenant_id=tid,
                                    created=1_790_013_891))
    r = _post(client, raw, header)
    assert r.json()["stale"] is False, "the subscription event is not stale: it is the sale"
    assert r.json()["handled"] is True

    sub = billing.get(SessionLocal(), tid)
    assert sub.status == "active", "a paid customer must not be left at incomplete/trialing"
    assert sub.plan == "home" and sub.interval == "month"
    assert sub.current_period_end is not None


def test_a_session_does_not_hold_back_a_later_subscription_snapshot(client, signed):
    """The other half: exempting the session from the watermark must not
    exempt subscription events from each other. An older snapshot arriving
    after a newer one is still dropped -- that is what the guard is for."""
    _uid, tid = seed_user(); login(client)
    client.post("/api/v1/billing/trial")

    raw, header = signed(_sub_event("evt_cancel", tenant_id=tid, status="canceled",
                                    created=1_790_000_200,
                                    etype="customer.subscription.updated"))
    assert _post(client, raw, header).json()["handled"] is True

    # A session stamped later must not become the new watermark either...
    session_evt = {
        "id": "evt_co_late", "type": "checkout.session.completed", "created": 1_790_000_300,
        "data": {"object": {"id": "cs_3", "object": "checkout.session",
                            "customer": "cus_test_1", "subscription": "sub_test_1",
                            "client_reference_id": tid, "metadata": {"tenant_id": tid}}},
    }
    raw, header = signed(session_evt)
    assert _post(client, raw, header).json()["handled"] is True

    # ...and the stale `active` from before the cancellation is still refused.
    raw, header = signed(_sub_event("evt_stale_active", tenant_id=tid, status="active",
                                    created=1_790_000_100,
                                    etype="customer.subscription.updated"))
    assert _post(client, raw, header).json()["stale"] is True
    assert billing.get(SessionLocal(), tid).status == "canceled"


def test_an_unrecognised_lookup_key_leaves_the_stored_plan_alone(client, signed):
    _uid, tid = seed_user(); login(client)
    client.post("/api/v1/billing/trial")
    raw, header = signed(_sub_event("evt_1", tenant_id=tid, lookup_key="guardian_home_month"))
    _post(client, raw, header)
    raw, header = signed(_sub_event("evt_2", tenant_id=tid, lookup_key="legacy_pricing_2019",
                                    created=1_700_000_100,
                                    etype="customer.subscription.updated"))
    assert _post(client, raw, header).json()["handled"] is True
    # Still `home`: a wrong plan on the account is worse than a stale one,
    # because it is what the customer is shown they bought.
    assert billing.get(SessionLocal(), tid).plan == "home"


# --------------------------------------------------------------------------
# A payment problem never stops the monitoring
# --------------------------------------------------------------------------

def test_a_failed_payment_keeps_the_account_fully_monitored(client, signed):
    _uid, tid = seed_user(); login(client)
    client.post("/api/v1/billing/trial")
    raw, header = signed(_sub_event("evt_a", tenant_id=tid))
    _post(client, raw, header)

    fail = {"id": "evt_fail", "type": "invoice.payment_failed", "created": 1_700_000_100,
            "data": {"object": {"id": "in_1", "customer": "cus_test_1",
                                "subscription": "sub_test_1"}}}
    raw, header = signed(fail)
    assert _post(client, raw, header).json()["handled"] is True

    sub = billing.get(SessionLocal(), tid)
    assert sub.status == "past_due"
    # The whole point: collection continues.
    assert billing.ingest_allowed(sub) is True
    assert "Monitoring is unaffected" in billing.notice(sub)

    ok = {"id": "evt_paid", "type": "invoice.payment_succeeded", "created": 1_700_000_200,
          "data": {"object": {"id": "in_2", "customer": "cus_test_1",
                              "subscription": "sub_test_1",
                              "lines": {"data": [{"period": {"end": 1_700_900_000}}]}}}}
    raw, header = signed(ok)
    _post(client, raw, header)
    assert billing.get(SessionLocal(), tid).status == "active"


def test_a_past_due_sensor_still_ingests_but_a_paused_one_does_not(client):
    _uid, tid = seed_user(); login(client)
    r = client.post("/api/v1/tenants/enrol-code") if False else None  # noqa: F841

    db = SessionLocal()
    sensor = m.Sensor(tenant_id=tid, name="s", status="active",
                      token_hash=__import__("guardian_hub.security", fromlist=["x"]).hash_token("tok-1"))
    db.add(sensor); db.commit()
    hdr = {"authorization": "Bearer tok-1"}
    payload = {"version": "1", "generated": dt.datetime.now(dt.timezone.utc).isoformat()}

    # No subscription row at all -- every tenant the owner set up by hand
    # before billing existed. Must still ingest.
    assert client.post("/api/v1/sensor/ingest", json=payload, headers=hdr).status_code == 200

    sub = billing.start_trial(db, tid)
    assert client.post("/api/v1/sensor/ingest", json=payload, headers=hdr).status_code == 200

    sub.status = "past_due"; db.commit()
    assert client.post("/api/v1/sensor/ingest", json=payload, headers=hdr).status_code == 200

    sub.status = "paused"; db.commit()
    r = client.post("/api/v1/sensor/ingest", json=payload, headers=hdr)
    assert r.status_code == 402 and "Existing data is unaffected" in r.json()["detail"]
    # Read paths are untouched: nothing was destroyed and the dashboard works.
    assert client.get("/api/v1/overview").status_code == 200
    assert client.get("/api/v1/sensor/config", headers=hdr).status_code == 200


# --------------------------------------------------------------------------
# The trial lifecycle
# --------------------------------------------------------------------------

def test_starting_a_trial_takes_no_card_and_creates_no_stripe_object(client):
    _uid, tid = seed_user(); login(client)
    body = client.post("/api/v1/billing/trial").json()
    assert body["subscription"]["plan"] == "trial"
    assert body["subscription"]["status"] == "trialing"
    assert body["subscription"]["interval"] == "none"
    assert body["subscription"]["price_label"] == "Free"
    assert body["subscription"]["ingesting"] is True

    sub = billing.get(SessionLocal(), tid)
    assert sub.stripe_customer_id == "" and sub.stripe_subscription_id == ""
    assert sub.trial_end is not None
    left = (sub.trial_end - dt.datetime.now(dt.timezone.utc)).days
    assert left in (plans.TRIAL_DAYS - 1, plans.TRIAL_DAYS)

    # A second claim is refused rather than handing out another three months.
    assert client.post("/api/v1/billing/trial").status_code == 409
    assert SessionLocal().query(m.Subscription).filter_by(tenant_id=tid).count() == 1


def test_start_trial_never_overwrites_a_paid_plan(client):
    # `client` is taken purely for the clean database it brings: the
    # conftest fixture drop_all/create_all's per test, so a trial another
    # test left lying about cannot be swept up by this one.
    db = SessionLocal()
    t = m.Tenant(slug="paid", name="Paid"); db.add(t); db.commit()
    db.add(m.Subscription(tenant_id=t.id, plan="business_pro", interval="year",
                          status="active")); db.commit()
    again = billing.start_trial(db, t.id)
    assert again.plan == "business_pro" and again.status == "active"


def test_a_trial_warns_before_it_ends(client):
    db = SessionLocal()
    t = m.Tenant(slug="warn", name="Warn"); db.add(t); db.commit()
    now = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
    sub = billing.start_trial(db, t.id, days=90, now=now)

    assert billing.notice(sub, now=now) == ""                       # 90 days out
    assert billing.trial_days_left(sub, now=now) == 90
    near = now + dt.timedelta(days=90 - 10)
    assert "ends in 10 days" in billing.notice(sub, now=near)
    one = now + dt.timedelta(days=89)
    assert "ends in 1 day" in billing.notice(sub, now=one)
    last = now + dt.timedelta(days=90) - dt.timedelta(hours=1)
    assert "ends today" in billing.notice(sub, now=last)

    # The one-shot latch for an out-of-app nudge fires exactly once.
    assert billing.mark_trial_warned(db, sub, now=near) is True
    assert billing.mark_trial_warned(db, sub, now=near) is False


def test_an_elapsed_trial_pauses_and_destroys_nothing(client):
    db = SessionLocal()
    t = m.Tenant(slug="lapse", name="Lapse"); db.add(t); db.commit()
    now = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
    sub = billing.start_trial(db, t.id, days=90, now=now)
    db.add(m.Scan(tenant_id=t.id, ts=now.isoformat(), raw={"devices": []}))
    db.commit()

    # A day early: nothing happens.
    # `expire_trials` returns the rows it moved, not a count -- that list is
    # the only latch on the end-of-trial email (see scheduler.trial_expiry_job).
    assert billing.expire_trials(db, now=now + dt.timedelta(days=89)) == []
    assert billing.get(db, t.id).status == "trialing"

    after = now + dt.timedelta(days=91)
    assert len(billing.expire_trials(db, now=after)) == 1
    # Idempotent: a second pass in the same hour finds nothing.
    assert billing.expire_trials(db, now=after) == []

    db.expire_all()
    sub = billing.get(db, t.id)
    assert sub.status == "paused"
    assert billing.ingest_allowed(sub) is False
    # Nothing charged, nothing deleted, no card ever taken.
    assert sub.stripe_customer_id == "" and sub.stripe_subscription_id == ""
    assert db.query(m.Scan).filter_by(tenant_id=t.id).count() == 1
    assert "still here and still exportable" in billing.notice(sub, now=after)


def test_the_expiry_job_runs_on_the_scheduler(client):
    from guardian_hub import scheduler
    assert "trial-expiry" in {j.id for j in scheduler.jobs()}
    scheduler.trial_expiry_job()   # must not raise


# --------------------------------------------------------------------------
# Routes: roles, and the unconfigured hub
# --------------------------------------------------------------------------

def test_billing_is_admin_only(client):
    seed_user(username="ann", email="a@x.com", tenant_slug="other", role="operator")
    login(client, "ann", "a@x.com")
    assert client.get("/api/v1/billing").status_code == 403
    assert client.post("/api/v1/billing/trial").status_code == 403
    assert client.post("/api/v1/billing/checkout",
                       json={"plan": "home", "interval": "month"}).status_code == 403
    assert client.post("/api/v1/billing/portal").status_code == 403


def test_an_unconfigured_hub_reports_it_rather_than_crashing(client, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "")
    seed_user(); login(client)
    body = client.get("/api/v1/billing").json()
    assert body["configured"] is False and body["portal_available"] is False
    # The catalogue and the trial are entirely local, so both still work.
    assert len(body["plans"]) == 5
    assert client.post("/api/v1/billing/trial").status_code == 200
    for r in (client.post("/api/v1/billing/checkout", json={"plan": "home", "interval": "month"}),
              client.post("/api/v1/billing/portal")):
        assert r.status_code == 503 and "not configured" in r.json()["detail"]


def test_the_trial_cannot_be_bought_and_a_bad_interval_is_refused(client, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_not_a_real_key")
    seed_user(); login(client)
    assert client.post("/api/v1/billing/checkout",
                       json={"plan": "trial", "interval": "month"}).status_code == 422
    assert client.post("/api/v1/billing/checkout",
                       json={"plan": "nope", "interval": "month"}).status_code == 422
    assert client.post("/api/v1/billing/checkout",
                       json={"plan": "home", "interval": "week"}).status_code == 422
    assert client.post("/api/v1/billing/checkout",
                       json={"plan": "home", "interval": "month", "quantity": 0}).status_code == 422


def test_checkout_asks_stripe_for_the_right_price_and_returns_its_url(client, monkeypatch):
    """The only test that exercises the outbound path, with the HTTP layer
    replaced. There are no Stripe credentials in this environment, so what is
    asserted is the request Vault *makes* -- the lookup key, the quantity
    and the tenant metadata -- not a real session."""
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_not_a_real_key")
    _uid, tid = seed_user(); login(client)

    calls: list[tuple[str, str, dict]] = []

    def fake_request(method, path, data=None, *, idempotency_key="", api_version=""):
        calls.append((method, path, data or {}))
        if path == "/v1/prices":
            return {"data": [{"id": "price_live_1"}]}
        if path == "/v1/checkout/sessions":
            return {"id": "cs_1", "url": "https://checkout.stripe.test/c/cs_1"}
        if path == "/v1/billing_portal/sessions":
            return {"id": "bps_1", "url": "https://billing.stripe.test/p/bps_1"}
        raise AssertionError(path)

    monkeypatch.setattr(stripe_api, "_request", fake_request)

    r = client.post("/api/v1/billing/checkout",
                    json={"plan": "business_essentials", "interval": "year", "quantity": 4})
    assert r.status_code == 200
    assert r.json()["url"] == "https://checkout.stripe.test/c/cs_1"

    price_call = next(c for c in calls if c[1] == "/v1/prices")
    assert price_call[2]["lookup_keys"] == ["guardian_business_essentials_year"]
    assert price_call[2]["active"] is True

    session_call = next(c for c in calls if c[1] == "/v1/checkout/sessions")
    body = session_call[2]
    assert body["mode"] == "subscription"
    assert body["line_items"] == [{"price": "price_live_1", "quantity": 4}]
    assert body["client_reference_id"] == tid
    assert body["subscription_data"]["metadata"]["tenant_id"] == tid

    # A per-account plan ignores the quantity: the price is not per site.
    calls.clear()
    client.post("/api/v1/billing/checkout", json={"plan": "business_pro",
                                                 "interval": "month", "quantity": 9})
    assert next(c for c in calls if c[1] == "/v1/checkout/sessions")[2]["line_items"][0]["quantity"] == 1

    # The portal needs a customer id, which only a completed checkout supplies.
    assert client.post("/api/v1/billing/portal").status_code == 409
    db = SessionLocal()
    billing.get(db, tid).stripe_customer_id = "cus_test_1"; db.commit()
    r = client.post("/api/v1/billing/portal")
    assert r.status_code == 200 and r.json()["url"].startswith("https://billing.stripe.test/")

    rows = SessionLocal().query(m.Audit).filter(m.Audit.action.like("billing.%")).all()
    assert {"billing.checkout_start", "billing.portal_open"} <= {r.action for r in rows}


def test_re_quoting_a_per_site_plan_starts_a_new_session_rather_than_replaying(client, monkeypatch):
    """Changing the site count changes the checkout idempotency key.

    Stripe answers a repeated idempotency key by replaying the *first*
    response and never reading the new parameters, so a key that left the
    quantity out would hand an admin who re-quoted 3 sites as 8 the 3-site
    session back -- they pay it believing it is the 8-site one and find out
    on the invoice. A flat plan must still collapse its retries, which is
    what the key is for at all, and it does because the key carries the
    *effective* quantity: 1 however many sites were claimed.
    """
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_not_a_real_key")
    seed_user(); login(client)

    keys: list[str] = []

    def fake_request(method, path, data=None, *, idempotency_key="", api_version=""):
        if path == "/v1/prices":
            return {"data": [{"id": "price_live_1"}]}
        if path == "/v1/checkout/sessions":
            keys.append(idempotency_key)
            return {"id": "cs_1", "url": "https://checkout.stripe.test/c/cs_1"}
        raise AssertionError(path)

    monkeypatch.setattr(stripe_api, "_request", fake_request)

    def identity(plan: str, quantity: int) -> str:
        """The key one checkout sent, minus its trailing clock stamp.

        The stamp is dropped rather than asserted on because a test that
        happened to straddle a UTC hour boundary would otherwise fail for a
        reason that has nothing to do with quantity. Its width is checked
        instead: `%Y%m%d%H` is ten digits, i.e. the window is an hour.
        """
        keys.clear()
        r = client.post("/api/v1/billing/checkout",
                        json={"plan": plan, "interval": "month", "quantity": quantity})
        assert r.status_code == 200, r.text
        assert len(keys) == 1
        # Two fixes for the same bug met here. This branch put the effective
        # quantity INTO the key; `stripe_api.create_checkout_session` also
        # appends a digest of the whole request after the clock stamp, so a
        # changed body of any kind gets its own key. Both are kept: the
        # quantity stays legible in the key for the audit trail, the digest
        # covers everything else. Peel the digest, then the stamp.
        head, _, digest = keys[0].rpartition(":")
        assert len(digest) == 16 and all(c in "0123456789abcdef" for c in digest), keys[0]
        head, _, stamp = head.rpartition(":")
        assert len(stamp) == 10 and stamp.isdigit(), keys[0]
        return head

    three, eight = identity("business_essentials", 3), identity("business_essentials", 8)
    assert three != eight
    assert three.endswith(":3") and eight.endswith(":8")

    # `partner` is the other per-site plan, and priced per site the same way.
    assert identity("partner", 2) != identity("partner", 5)

    # A flat plan is one subscription whatever the caller claims, so its
    # repeats stay collapsed -- which is the property that would break if the
    # key used the request's quantity instead of the effective one.
    assert identity("business_pro", 1) == identity("business_pro", 9)


def test_a_stripe_error_is_a_502_and_never_leaks_the_request(client, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_not_a_real_key")
    seed_user(); login(client)

    def boom(*_a, **_k):
        raise stripe_api.StripeError("Your card was declined.")

    monkeypatch.setattr(stripe_api, "_request", boom)
    r = client.post("/api/v1/billing/checkout", json={"plan": "home", "interval": "month"})
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "declined" in detail
    assert "sk_test" not in detail and "Authorization" not in detail


def test_the_billing_tables_exist(client):
    from sqlalchemy import inspect
    from guardian_hub.db import engine
    assert {"subscription", "billing_event"} <= set(inspect(engine).get_table_names())


# --------------------------------------------------------------------------
# An abandoned checkout costs nothing
# --------------------------------------------------------------------------

def _stub_stripe(monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_not_a_real_key")

    def fake_request(method, path, data=None, *, idempotency_key="", api_version=""):
        if path == "/v1/prices":
            return {"data": [{"id": "price_live_1"}]}
        return {"id": "cs_1", "url": "https://checkout.stripe.test/c/cs_1"}

    monkeypatch.setattr(stripe_api, "_request", fake_request)


def test_an_abandoned_checkout_does_not_cost_the_trial_or_block_a_retry(client, monkeypatch):
    """Closing the Stripe tab is the ordinary case, not an edge one.

    The placeholder row has to exist (it is how the webhook finds the tenant
    later), but it must not read as a subscription: the free trial is the
    whole funnel, and the plan they just abandoned is the one they are most
    likely to retry.
    """
    _uid, tid = seed_user(); login(client)
    _stub_stripe(monkeypatch)

    assert client.post("/api/v1/billing/checkout",
                       json={"plan": "home", "interval": "month"}).status_code == 200
    sub = billing.get(SessionLocal(), tid)
    assert sub is not None and sub.status == "incomplete"
    assert billing.is_uncommitted(sub) is True

    body = client.get("/api/v1/billing").json()
    # No plan is claimed, so the Home card is still choosable...
    assert all(c["current"] is False for c in body["plans"])
    # ...and nothing tells the customer a payment failed that never happened.
    assert body["notice"] == ""
    # Monitoring is untouched.
    assert billing.ingest_allowed(sub) is True
    assert body["subscription"]["ingesting"] is True

    # Starting a different checkout re-points the placeholder rather than
    # colliding with it.
    assert client.post("/api/v1/billing/checkout",
                       json={"plan": "partner", "interval": "year"}).status_code == 200
    assert billing.get(SessionLocal(), tid).plan == "partner"

    # And the free trial is still on offer.
    r = client.post("/api/v1/billing/trial")
    assert r.status_code == 200
    assert r.json()["subscription"]["status"] == "trialing"
    assert r.json()["subscription"]["plan"] == "trial"
    assert SessionLocal().query(m.Subscription).filter_by(tenant_id=tid).count() == 1
    # Once it IS a trial, a second claim is refused again.
    assert client.post("/api/v1/billing/trial").status_code == 409


def test_a_real_stripe_subscription_is_never_treated_as_uncommitted(client, monkeypatch):
    _uid, tid = seed_user(); login(client)
    db = SessionLocal()
    db.add(m.Subscription(tenant_id=tid, plan="home", interval="month",
                          status="incomplete", stripe_subscription_id="sub_real_1"))
    db.commit()
    sub = billing.get(db, tid)
    assert billing.is_uncommitted(sub) is False
    assert client.post("/api/v1/billing/trial").status_code == 409


def test_the_renewal_date_is_read_from_either_stripe_payload_shape(client, signed):
    """`current_period_end` moved from the subscription object onto its line
    items in newer API versions, and which shape arrives is decided by the
    version configured on the WEBHOOK ENDPOINT -- not by the Stripe-Version
    header this hub sends outbound. Both are read."""
    _uid, tid = seed_user(); login(client)
    client.post("/api/v1/billing/trial")

    newer = _sub_event("evt_newshape", tenant_id=tid, created=1_700_000_000)
    obj = newer["data"]["object"]
    del obj["current_period_end"]
    obj["items"]["data"][0]["current_period_end"] = 1_700_600_000
    raw, header = signed(newer)
    assert _post(client, raw, header).json()["handled"] is True

    end = billing.get(SessionLocal(), tid).current_period_end
    assert end is not None and int(end.timestamp()) == 1_700_600_000


def test_a_concurrent_replay_is_a_duplicate_not_a_500(client, signed, monkeypatch):
    """Two of Stripe's retries can get past the `db.get` check at once; the
    primary key decides it. The loser must answer 200 as a duplicate, because
    a 500 has Stripe redelivering a handled event for days."""
    _uid, tid = seed_user(); login(client)
    client.post("/api/v1/billing/trial")

    # The race is simulated from the other end: apply the event for real,
    # then replay it with `db.get` blinded to BillingEvent, so the route
    # takes exactly the path a retry that lost the race would.
    raw, header = signed(_sub_event("evt_race", tenant_id=tid))
    assert _post(client, raw, header).status_code == 200

    def blind(db, model, ident):
        return None if model is m.BillingEvent else DbSessionGet(db, model, ident)

    DbSessionGet = type(SessionLocal()).get
    monkeypatch.setattr(type(SessionLocal()), "get", blind, raising=True)
    r = _post(client, raw, header)
    monkeypatch.undo()
    assert r.status_code == 200 and r.json()["duplicate"] is True
    assert SessionLocal().query(m.BillingEvent).filter_by(id="evt_race").count() == 1
