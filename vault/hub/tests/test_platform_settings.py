"""Platform settings: the owner changes how the product behaves, live.

The failure this file is really guarding against is not a bad write -- it is
an accepted write that nothing reads. A setting the console saves and the
product ignores is worse than a read-only one, because the owner watches it
succeed and then watches nothing change. So the important tests here assert
that a saved value is what the BEHAVIOUR uses afterwards, not merely what the
settings endpoint echoes back.
"""
import datetime as dt

import pytest

from guardian_hub import billing, models as m, platform_settings as ps
from guardian_hub.db import SessionLocal
from tests.test_auth import login, seed_user


@pytest.fixture(autouse=True)
def _clean_cache(client):
    """The accessors cache in-process, so a test that writes must not leak its
    value into the next one.

    Takes `client` so the tables exist before the cache is primed -- the
    accessors are read by ordinary product code, which runs against a
    migrated database, not an empty one.
    """
    ps.refresh()
    yield
    db = SessionLocal()
    db.query(m.PlatformSetting).delete()
    db.commit(); db.close()
    ps.refresh()


def _owner(client):
    uid, tid = seed_user(username="miro", email="m@example.com", tenant_slug="home")
    db = SessionLocal(); db.get(m.User, uid).is_superadmin = True; db.commit(); db.close()
    login(client)
    return tid


# --- authorisation --------------------------------------------------------

def test_a_tenant_admin_cannot_read_or_change_platform_settings(client):
    seed_user(role="admin")
    login(client)
    assert client.get("/api/v1/platform/settings").status_code == 403
    assert client.post("/api/v1/platform/settings",
                       json={"key": "trial_days", "value": "30"}).status_code == 403


def test_signed_out_is_refused(client):
    seed_user()
    assert client.get("/api/v1/platform/settings").status_code in (401, 403)


# --- what the page shows --------------------------------------------------

def test_defaults_are_reported_as_defaults_not_as_choices_somebody_made(client):
    _owner(client)
    body = client.get("/api/v1/platform/settings").json()
    fields = {f["key"]: f for f in body["fields"]}
    assert fields["trial_days"]["value"] == "90"
    assert fields["trial_days"]["overridden"] is False
    assert fields["billing_mode"]["choices"] == ["trial_only", "plans"]


def test_credentials_are_reported_but_never_returned(client):
    _owner(client)
    body = client.get("/api/v1/platform/settings").json()
    keys = {s["key"] for s in body["secrets"]}
    assert {"smtp", "stripe", "groq"} <= keys
    blob = repr(body)
    # A secret row carries a label and a boolean. Nothing else.
    for s in body["secrets"]:
        assert set(s) == {"key", "label", "set"}
    assert "sk_" not in blob and "re_" not in blob


# --- a saved value is the value the product uses --------------------------

def test_shortening_the_warning_window_changes_who_gets_warned(client):
    """`trials_to_warn` used to bind the window as a DEFAULT ARGUMENT, which
    is evaluated once at import -- so this is the test that would have caught
    a setting that saves and does nothing."""
    tid = _owner(client)
    now = dt.datetime.now(dt.timezone.utc)
    db = SessionLocal()
    db.add(m.Subscription(tenant_id=tid, plan="trial", interval="none", status="trialing",
                          trial_end=now + dt.timedelta(days=10), created=now, updated=now))
    db.commit(); db.close()

    db = SessionLocal()
    assert len(billing.trials_to_warn(db, now)) == 1          # default window is 14 days
    db.close()

    r = client.post("/api/v1/platform/settings", json={"key": "trial_warn_days", "value": "5"})
    assert r.status_code == 200, r.text

    db = SessionLocal()
    assert billing.trials_to_warn(db, now) == []              # 10 days out, window is 5
    db.close()


def test_switching_the_billing_mode_changes_what_the_product_offers(client):
    _owner(client)
    assert ps.trial_only() is True
    r = client.post("/api/v1/platform/settings", json={"key": "billing_mode", "value": "plans"})
    assert r.status_code == 200, r.text
    assert ps.trial_only() is False
    assert ps.billing_mode() == "plans"


def test_a_new_trial_uses_the_new_length_and_running_trials_keep_their_date(client):
    """The semantics, written down: this moves NEW trials only. A stored end
    date a customer has already been told must not shift under them."""
    tid = _owner(client)
    now = dt.datetime.now(dt.timezone.utc)
    db = SessionLocal()
    existing = m.Subscription(tenant_id=tid, plan="trial", interval="none", status="trialing",
                              trial_end=now + dt.timedelta(days=80), created=now, updated=now)
    db.add(existing); db.commit()
    before = existing.trial_end
    db.close()

    client.post("/api/v1/platform/settings", json={"key": "trial_days", "value": "30"})
    assert ps.trial_days() == 30

    db = SessionLocal()
    # The running trial is untouched...
    assert db.query(m.Subscription).filter_by(tenant_id=tid).one().trial_end == before
    # ...and a brand new one gets the new length.
    other = m.Tenant(slug="fresh", name="Fresh Co")
    db.add(other); db.commit()
    sub = billing.start_trial(db, other.id, now=now)
    assert (sub.trial_end - now).days == 30
    db.close()


def test_the_support_address_is_where_a_new_ticket_goes(client):
    """Changing the mailbox must change delivery, not just the label."""
    from guardian_hub import mailer
    tid = _owner(client)
    client.post("/api/v1/platform/settings",
                json={"key": "support_email", "value": "help@guarder.io"})
    # Cleared rather than index-sliced: `mailer.SENT` is capped at 50, so an
    # index taken before a long run is not a reliable starting point.
    mailer.SENT.clear()
    r = client.post("/api/v1/support/tickets",
                    json={"subject": "Testing", "body": "Just checking."})
    assert r.status_code == 200, r.text
    assert any(to == "help@guarder.io" for to, _s, _t in mailer.SENT)


# --- validation -----------------------------------------------------------

def test_out_of_range_and_unknown_keys_are_refused_with_a_readable_reason(client):
    _owner(client)
    r = client.post("/api/v1/platform/settings", json={"key": "trial_days", "value": "2"})
    assert r.status_code == 422 and "at least" in r.text
    r = client.post("/api/v1/platform/settings", json={"key": "trial_days", "value": "ninety"})
    assert r.status_code == 422 and "whole number" in r.text
    r = client.post("/api/v1/platform/settings", json={"key": "billing_mode", "value": "freebies"})
    assert r.status_code == 422
    # A key nothing reads must never be storable: that is the whole failure
    # mode this module exists to prevent.
    r = client.post("/api/v1/platform/settings", json={"key": "smtp_password", "value": "x"})
    assert r.status_code == 422
    db = SessionLocal()
    assert db.query(m.PlatformSetting).filter_by(key="smtp_password").one_or_none() is None
    db.close()


def test_a_change_is_audited_against_the_platform_not_a_customer(client):
    """`Audit.tenant_id` must be NULL. The tenant-scoped audit page in
    Settings filters on that column, so a platform row that borrowed a
    customer's id would publish the operator's own changes into that
    customer's audit log."""
    tid = _owner(client)
    client.post("/api/v1/platform/settings", json={"key": "trial_days", "value": "120"})
    db = SessionLocal()
    row = (db.query(m.Audit).filter_by(action="platform.setting")
             .order_by(m.Audit.ts.desc()).first())
    assert row is not None
    assert row.body["key"] == "trial_days" and row.body["value"] == "120"
    assert row.user == "miro"
    assert row.tenant_id is None
    # And it is invisible to a tenant-scoped query, which is the point.
    assert db.query(m.Audit).filter(m.Audit.tenant_id == tid,
                                    m.Audit.action == "platform.setting").count() == 0
    db.close()


def test_a_stored_row_for_a_retired_key_is_ignored_not_fatal(client):
    _owner(client)
    db = SessionLocal()
    db.add(m.PlatformSetting(key="something_we_removed", value="whatever"))
    db.commit(); db.close()
    ps.refresh()
    assert client.get("/api/v1/platform/settings").status_code == 200
    assert ps.trial_days() == 90
