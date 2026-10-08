"""The owner can extend a customer's trial or grant them a plan without a
charge, from the console -- and cannot touch anything Stripe is billing.

Why the boundary is where it is: a paid subscription's `plan`, `interval` and
`status` are written by `billing.apply_event` from Stripe's own events, so a
console edit to one of those rows would hold only until the next webhook,
and the ledger would be wrong in between without anyone being told. Trials
and comped plans have no Stripe objects at all (`start_trial`'s docstring:
"Creates NO Stripe objects"), so those are Vault's to set. The refusal is
the feature: it says where to go instead.
"""

import datetime as dt

import pytest

from guardian_hub import billing, models as m
from guardian_hub.config import settings
from guardian_hub.db import SessionLocal

from tests.test_auth import login, seed_user


def _owner_and_customer():
    """A superadmin signed in, plus a customer tenant they do not belong to."""
    uid, _ = seed_user(username="miro", email="m@example.com", tenant_slug="home")
    db = SessionLocal()
    db.get(m.User, uid).is_superadmin = True
    other = m.Tenant(slug="northwind", name="Northwind Dental")
    db.add(other); db.commit()
    tid = other.id
    db.close()
    return tid


def _sub(tid) -> m.Subscription:
    db = SessionLocal()
    try:
        return db.query(m.Subscription).filter_by(tenant_id=tid).one()
    finally:
        db.close()


def _now():
    return dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------
# billing.extend_trial / billing.grant_plan -- the helpers
# --------------------------------------------------------------------------

def test_extending_a_trial_moves_its_end_and_rearms_the_warning(client):
    tid = _owner_and_customer()
    db = SessionLocal()
    sub = billing.start_trial(db, tid, days=3)
    sub.trial_warned = _now()          # the 14-day letter already went out
    db.commit()
    before = sub.trial_end

    billing.extend_trial(db, tid, days=30)
    db.close()

    sub = _sub(tid)
    assert sub.status == "trialing" and sub.plan == "trial" and sub.interval == "none"
    assert sub.trial_end == before + dt.timedelta(days=30)
    # A longer trial has a new "two weeks left" -- the letter must be able to
    # go out again rather than being suppressed by the one already sent.
    assert sub.trial_warned is None


def test_extending_a_lapsed_trial_revives_it_from_today(client):
    """A trial that ran out is `paused`: data kept, ingest refused. Extending
    it counts from now, not from the day it lapsed -- "give them two more
    weeks" means two weeks of service, not two weeks minus however long they
    sat paused."""
    tid = _owner_and_customer()
    db = SessionLocal()
    sub = billing.start_trial(db, tid, days=1)
    sub.status = "paused"
    sub.trial_end = _now() - dt.timedelta(days=20)
    db.commit()

    billing.extend_trial(db, tid, days=14)
    db.close()

    sub = _sub(tid)
    assert sub.status == "trialing"
    assert billing.ingest_allowed(sub)
    assert abs((sub.trial_end - (_now() + dt.timedelta(days=14))).total_seconds()) < 120


def test_granting_a_plan_makes_it_active_with_nothing_owed(client):
    tid = _owner_and_customer()
    db = SessionLocal()
    billing.start_trial(db, tid)
    billing.grant_plan(db, tid, plan="business_pro", interval="year")
    db.close()

    sub = _sub(tid)
    assert (sub.plan, sub.interval, sub.status) == ("business_pro", "year", "active")
    assert sub.trial_end is None
    assert sub.current_period_end is None and sub.cancel_at_period_end is False
    # Still no Stripe objects: the comp is Vault's record, like the trial.
    assert sub.stripe_customer_id == "" and sub.stripe_subscription_id == ""


@pytest.mark.parametrize("call", [
    lambda db, tid: billing.extend_trial(db, tid, days=7),
    lambda db, tid: billing.grant_plan(db, tid, plan="home", interval="month"),
])
def test_a_customer_stripe_is_billing_cannot_be_edited_here(client, call):
    tid = _owner_and_customer()
    db = SessionLocal()
    sub = billing.start_trial(db, tid)
    sub.status, sub.plan, sub.interval = "active", "home", "month"
    sub.stripe_customer_id, sub.stripe_subscription_id = "cus_live1", "sub_live1"
    db.commit()

    with pytest.raises(billing.StripeOwnsThis) as exc:
        call(db, tid)
    db.close()
    assert "cus_live1" in exc.value.dashboard_url
    assert _sub(tid).plan == "home"        # untouched


def test_a_stripe_subscription_that_ended_is_guardians_again(client):
    """`canceled` still carries the old Stripe ids, but Stripe is no longer
    billing anyone: the owner may put that customer back on a trial."""
    tid = _owner_and_customer()
    db = SessionLocal()
    sub = billing.start_trial(db, tid)
    sub.status, sub.stripe_subscription_id = "canceled", "sub_old"
    db.commit()
    billing.extend_trial(db, tid, days=14)
    db.close()
    assert _sub(tid).status == "trialing"


@pytest.mark.parametrize("bad", [dict(plan="trial", interval="month"),
                                 dict(plan="nope", interval="month"),
                                 dict(plan="home", interval="weekly")])
def test_a_grant_must_name_a_paid_plan_and_a_real_interval(client, bad):
    tid = _owner_and_customer()
    db = SessionLocal()
    billing.start_trial(db, tid)
    with pytest.raises(ValueError):
        billing.grant_plan(db, tid, **bad)
    db.close()


# --------------------------------------------------------------------------
# POST /platform/tenants/{tid}/billing -- the route
# --------------------------------------------------------------------------

def _post(client, tid, body):
    return client.post(f"/api/v1/platform/tenants/{tid}/billing", json=body)


def test_the_owner_extends_a_trial_and_sees_it_in_the_account_view(client):
    tid = _owner_and_customer()
    db = SessionLocal(); billing.start_trial(db, tid, days=5); db.close()
    login(client, "miro", "m@example.com")

    r = _post(client, tid, {"action": "extend_trial", "days": 30})
    assert r.status_code == 200, r.text
    b = r.json()["billing"]
    assert b["status"] == "trialing" and b["trial_days_left"] >= 34


def test_the_owner_grants_a_plan_and_the_customer_sees_it_live_ish(client):
    tid = _owner_and_customer()
    db = SessionLocal(); billing.start_trial(db, tid); db.close()
    login(client, "miro", "m@example.com")

    r = _post(client, tid, {"action": "grant_plan", "plan": "partner", "interval": "month"})
    assert r.status_code == 200, r.text
    b = r.json()["billing"]
    assert b["plan"] == "partner" and b["status"] == "active" and b["interval"] == "month"
    # Comped means nothing owed: the ledger must not count it as revenue.
    assert b["mrr_cents"] == 0


def test_a_stripe_billed_customer_gets_a_409_that_says_where_to_go(client):
    tid = _owner_and_customer()
    db = SessionLocal()
    sub = billing.start_trial(db, tid)
    sub.status, sub.plan, sub.stripe_customer_id, sub.stripe_subscription_id = "active", "home", "cus_9", "sub_9"
    db.commit(); db.close()
    login(client, "miro", "m@example.com")

    r = _post(client, tid, {"action": "grant_plan", "plan": "partner", "interval": "month"})
    assert r.status_code == 409, r.text
    assert "dashboard.stripe.com" in r.text and "cus_9" in r.text


@pytest.mark.parametrize("body", [
    {"action": "extend_trial", "days": 0},
    {"action": "extend_trial", "days": 366},
    {"action": "grant_plan", "plan": "trial", "interval": "month"},
    {"action": "grant_plan", "plan": "home", "interval": "weekly"},
    {"action": "refund_everything"},
])
def test_bad_requests_are_422_not_500(client, body):
    tid = _owner_and_customer()
    db = SessionLocal(); billing.start_trial(db, tid); db.close()
    login(client, "miro", "m@example.com")
    assert _post(client, tid, body).status_code == 422


def test_an_unknown_customer_is_404(client):
    _owner_and_customer()
    login(client, "miro", "m@example.com")
    assert _post(client, "no-such-tenant", {"action": "extend_trial", "days": 7}).status_code == 404


def test_a_tenant_admin_cannot_reach_it(client):
    tid = _owner_and_customer()
    seed_user(username="ann", email="a@example.com", tenant_slug="other", role="admin")
    login(client, "ann", "a@example.com")
    assert _post(client, tid, {"action": "extend_trial", "days": 7}).status_code == 403


def test_every_adjustment_is_on_the_audit_trail(client):
    tid = _owner_and_customer()
    db = SessionLocal(); billing.start_trial(db, tid); db.close()
    login(client, "miro", "m@example.com")
    _post(client, tid, {"action": "extend_trial", "days": 7})
    _post(client, tid, {"action": "grant_plan", "plan": "home", "interval": "year"})

    db = SessionLocal()
    rows = db.query(m.Audit).filter_by(tenant_id=tid, action="billing.admin").order_by(m.Audit.id).all()
    db.close()
    assert [r.target for r in rows] == ["extend_trial", "grant_plan"]
    assert rows[0].user == "miro"
    assert rows[1].body["plan"] == "home" and rows[1].body["interval"] == "year"


def test_the_account_view_links_to_the_customer_in_stripe(client, monkeypatch):
    """Charged plans change in Stripe. The console says so with a link, and
    the link knows which mode the key is in -- a test key's customers live
    under /test/, and a link that lands on the wrong dashboard is a hunt."""
    tid = _owner_and_customer()
    db = SessionLocal()
    sub = billing.start_trial(db, tid)
    sub.stripe_customer_id = "cus_42"
    db.commit(); db.close()
    login(client, "miro", "m@example.com")

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    b = client.get(f"/api/v1/platform/tenants/{tid}").json()["billing"]
    assert b["stripe_dashboard_url"] == "https://dashboard.stripe.com/test/customers/cus_42"

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_live_x")
    b = client.get(f"/api/v1/platform/tenants/{tid}").json()["billing"]
    assert b["stripe_dashboard_url"] == "https://dashboard.stripe.com/customers/cus_42"
