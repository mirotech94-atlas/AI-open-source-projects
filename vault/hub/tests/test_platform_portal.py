"""The owner portal's ledger: `GET /api/v1/platform`.

The only read in the product that crosses tenant boundaries, which makes the
authorisation test the important one: a tenant admin must not be able to see
the shape of anybody else's business, not even alongside their own row.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.networks import SENSOR_STALE_S
from tests.test_auth import login, seed_user


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _owner():
    """A superadmin, plus a second tenant they do not belong to."""
    uid, tid = seed_user(username="miro", email="m@example.com", tenant_slug="home")
    db = SessionLocal()
    db.get(m.User, uid).is_superadmin = True
    other = m.Tenant(slug="northwind", name="Northwind Dental")
    db.add(other); db.commit()
    other_id = other.id
    db.close()
    return uid, tid, other_id


def test_a_tenant_admin_cannot_read_the_platform_ledger(client):
    seed_user(role="admin")            # admin of their own tenant, not superadmin
    login(client)
    r = client.get("/api/v1/platform")
    assert r.status_code == 403, r.text
    assert "superadmin" in r.text.lower()


def test_signed_out_is_refused(client):
    seed_user()
    assert client.get("/api/v1/platform").status_code in (401, 403)


def test_the_owner_sees_every_tenant_including_ones_they_are_not_in(client):
    _owner()
    login(client)
    body = client.get("/api/v1/platform").json()
    slugs = [t["slug"] for t in body["tenants"]]
    assert "home" in slugs and "northwind" in slugs
    assert body["totals"]["tenants"] == len(body["tenants"]) == 2
    # Ordered by name so the ledger does not reshuffle between loads.
    assert [t["name"] for t in body["tenants"]] == sorted(t["name"] for t in body["tenants"])


def test_sensors_count_online_against_the_app_s_own_staleness_window(client):
    _uid, tid, _other = _owner()
    db = SessionLocal()
    fresh = _now() - dt.timedelta(seconds=SENSOR_STALE_S // 2)
    stale = _now() - dt.timedelta(seconds=SENSOR_STALE_S * 4)
    db.add_all([
        m.Sensor(tenant_id=tid, name="fresh", status="active", last_seen=fresh),
        m.Sensor(tenant_id=tid, name="stale", status="active", last_seen=stale),
        m.Sensor(tenant_id=tid, name="never", status="pending", last_seen=None),
        # A revoked sensor is not part of the estate and must not inflate the
        # denominator -- otherwise every decommissioned box makes the tenant
        # look permanently half-down.
        m.Sensor(tenant_id=tid, name="gone", status="revoked", last_seen=fresh),
    ])
    db.commit(); db.close()
    login(client)
    row = next(t for t in client.get("/api/v1/platform").json()["tenants"] if t["id"] == tid)
    assert row["sensors_total"] == 3
    assert row["sensors_online"] == 1
    assert row["last_scan"], "the most recent sensor contact should be reported"


def test_open_alerts_split_by_severity_and_info_is_ignored(client):
    _uid, tid, _other = _owner()
    db = SessionLocal()
    now = _now().isoformat()
    # `key` is unique per tenant, so the index keeps the two WARN rows apart.
    for i, (sev, status) in enumerate([("CRIT", "open"), ("WARN", "open"), ("WARN", "open"),
                                       ("INFO", "open"), ("CRIT", "resolved")]):
        db.add(m.Alert(tenant_id=tid, key=f"lan:probe:{i}", sev=sev, msg="x",
                       status=status, first_seen=now, last_seen=now))
    db.commit(); db.close()
    login(client)
    body = client.get("/api/v1/platform").json()
    row = next(t for t in body["tenants"] if t["id"] == tid)
    assert (row["open_crit"], row["open_warn"]) == (1, 2)
    assert body["totals"]["open_crit"] == 1 and body["totals"]["open_warn"] == 2


def test_a_paused_trial_shows_as_no_longer_ingesting(client):
    _uid, tid, other = _owner()
    db = SessionLocal()
    db.add(m.Subscription(tenant_id=tid, plan="trial", interval="month", status="paused",
                          trial_end=_now() - dt.timedelta(days=1)))
    db.add(m.Subscription(tenant_id=other, plan="trial", interval="month", status="trialing",
                          trial_end=_now() + dt.timedelta(days=3)))
    db.commit(); db.close()
    login(client)
    body = client.get("/api/v1/platform").json()
    rows = {t["id"]: t for t in body["tenants"]}
    assert rows[tid]["ingesting"] is False, "a paused tenant has stopped collecting"
    assert rows[tid]["plan_status"] == "paused"
    assert rows[other]["ingesting"] is True
    assert rows[other]["trial_days_left"] in (2, 3)
    # A trial inside the warning window is what the owner needs to act on.
    assert body["totals"]["trials_ending"] == 1
    assert body["totals"]["paused"] == 1


def test_pending_access_requests_are_counted_but_not_exposed_here(client):
    _owner()
    db = SessionLocal()
    db.add_all([m.Lead(name="A", email="a@x.test", status="pending"),
                m.Lead(name="B", email="b@x.test", status="approved")])
    db.commit(); db.close()
    login(client)
    body = client.get("/api/v1/platform").json()
    assert body["totals"]["open_requests"] == 1
    # The ledger counts them; the Requests page is where they are read.
    assert "a@x.test" not in client.get("/api/v1/platform").text


def test_it_reports_how_the_platform_is_wired_without_leaking_secrets(client):
    _owner()
    login(client)
    raw = client.get("/api/v1/platform").text
    body = client.get("/api/v1/platform").json()
    assert set(["mail_configured", "billing_mode", "base_url"]) <= set(body)
    assert isinstance(body["mail_configured"], bool)
    for secret in ("password", "api_key", "secret", "token_hash", "whsec", "sk_"):
        assert secret not in raw.lower(), f"the ledger leaks {secret}"
