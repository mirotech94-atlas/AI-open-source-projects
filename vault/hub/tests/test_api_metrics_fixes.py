"""Two endpoints that let a customer check Vault's arithmetic.

`/v1/metrics` is the more important one, and the trust plan (§2.6.7) is
right about why: every other route reports a conclusion, so an availability
figure nobody can recompute is a claim rather than a measurement. Shipping
the samples — with the same classification the dashboard applied — means a
skeptical client can reach Vault's own number or find that they cannot.
Publishing the means to falsify your headline figure is worth more than the
figure is.

That only holds if the two agree. The test that matters here is
`test_a_client_can_recompute_the_published_uptime`: it recomputes
availability from the endpoint and asserts it lands on exactly what
`/api/v1/sites` prints. If those ever diverge, this endpoint hands a
customer evidence that Vault does not reconcile with itself, which is
worse than not having shipped it.

`/v1/fixes` promotes the fix log out of `/v1/security`, where it was one
field on a large posture payload — awkward to audit and impossible to page,
for the most trust-critical dataset Vault holds.
"""
import datetime as dt

from guardian_hub import maintenance, models as m
from guardian_hub.db import SessionLocal
from tests.test_api_v1 import bearer, seeded


def _samples(client, secret, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = client.get(f"/v1/metrics?{qs}", headers=bearer(secret))
    assert r.status_code == 200, r.text
    return r.json()


def _add_up_samples(tid, name, ups, downs):
    db = SessionLocal()
    try:
        db.add(m.Site(tenant_id=tid, name=name, url=f"https://{name}.example",
                      last={"ok": True, "code": 200}))
        for when, val in [(w, 1.0) for w in ups] + [(w, 0.0) for w in downs]:
            db.add(m.Metric(tenant_id=tid, ts=when.isoformat(), name="up",
                            entity=name, value=val))
        db.commit()
    finally:
        db.close()


def _seed_series(client):
    tid, read_secret, _ = seeded(client)
    now = dt.datetime.now(dt.timezone.utc)
    ups = [now - dt.timedelta(minutes=10 * i) for i in range(3, 40)]
    downs = [now - dt.timedelta(hours=2) + dt.timedelta(minutes=i) for i in range(6)]
    _add_up_samples(tid, "shop", ups, downs)
    return tid, read_secret


# --------------------------------------------------------------------------
# /v1/metrics
# --------------------------------------------------------------------------

def test_samples_come_back_with_an_explicit_state(client):
    _tid, secret = _seed_series(client)
    items = _samples(client, secret, metric="up", monitor="shop", limit=1000)["items"]
    assert items, "the series should be readable"
    assert {i["state"] for i in items} == {"up", "down"}
    assert all(i["metric"] == "up" and i["monitor"] == "shop" for i in items)


def test_a_series_arrives_oldest_first_so_it_can_be_plotted(client):
    _tid, secret = _seed_series(client)
    items = _samples(client, secret, metric="up", monitor="shop", limit=1000)["items"]
    assert [i["ts"] for i in items] == sorted(i["ts"] for i in items)


def test_a_client_can_recompute_the_published_uptime(client):
    """The whole point of the endpoint, asserted against the real page."""
    _tid, secret = _seed_series(client)
    items = _samples(client, secret, metric="up", monitor="shop", hours=24, limit=1000)["items"]
    counted = [i for i in items if i["state"] in ("up", "down")]
    mine = int(10000 * sum(i["state"] == "up" for i in counted) / len(counted)) / 100

    card = next(c for c in client.get("/api/v1/sites").json()["sites"] if c["name"] == "shop")
    assert card["uptime_24h"] == mine
    assert card["uptime_24h_checks"] == len(counted)


def test_planned_maintenance_is_labelled_so_the_denominator_matches(client):
    """A sample inside a window must not come back as up/down: a client that
    counted it would build a different denominator from the dashboard's and
    conclude, wrongly, that Vault's figure was inflated."""
    tid, secret = _seed_series(client)
    outage = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        tenant.settings = maintenance.record(dict(tenant.settings or {}),
                                             outage - dt.timedelta(minutes=1),
                                             outage + dt.timedelta(minutes=10))
        db.commit()
    finally:
        db.close()

    items = _samples(client, secret, metric="up", monitor="shop", hours=24, limit=1000)["items"]
    assert "planned" in {i["state"] for i in items}
    assert not [i for i in items if i["state"] == "down"], (
        "the whole outage was inside the window, so nothing should read as down"
    )

    counted = [i for i in items if i["state"] in ("up", "down")]
    card = next(c for c in client.get("/api/v1/sites").json()["sites"] if c["name"] == "shop")
    assert card["uptime_24h_checks"] == len(counted)
    assert card["uptime_24h_planned"] == len([i for i in items if i["state"] == "planned"])


def test_a_series_where_up_down_is_meaningless_says_so_rather_than_guessing(client):
    _tid, secret = _seed_series(client)
    items = _samples(client, secret, metric="latency_ms", limit=50)["items"]
    assert all(i["state"] == "" for i in items if i["value"] is not None)


def test_metrics_are_scoped_to_the_callers_account(client):
    _tid, secret = _seed_series(client)
    other = _samples(client, secret, metric="up", monitor="not-a-site-here")
    assert other["items"] == [] and other["total"] == 0


# --------------------------------------------------------------------------
# /v1/fixes
# --------------------------------------------------------------------------

def test_the_fix_log_is_reachable_on_its_own(client):
    tid, secret, _ = seeded(client)
    db = SessionLocal()
    try:
        db.add(m.Fix(tenant_id=tid, ts=dt.datetime.now(dt.timezone.utc).isoformat(),
                     host="vps1", action="enable_ufw", label="Turn on the firewall",
                     by="auto", ok=True, out="ufw enabled\nrules reloaded"))
        db.commit()
    finally:
        db.close()
    r = client.get("/v1/fixes", headers=bearer(secret))
    assert r.status_code == 200, r.text
    row = r.json()["items"][0]
    assert row["host"] == "vps1" and row["action"] == "enable_ufw" and row["ok"] is True
    assert row["detail"] == "ufw enabled", "only the command's first line, not its whole tail"


def test_a_command_that_succeeded_is_not_reported_as_verified(client):
    """"Exited zero" and "a later sweep confirmed it" are different claims.
    Merging them is the self-flattery the plan objects to in §2.5D."""
    tid, secret, _ = seeded(client)
    db = SessionLocal()
    try:
        db.add(m.Fix(tenant_id=tid, ts=dt.datetime.now(dt.timezone.utc).isoformat(),
                     host="vps1", action="reboot", by="auto", ok=True, out=""))
        db.commit()
    finally:
        db.close()
    row = client.get("/v1/fixes", headers=bearer(secret)).json()["items"][0]
    assert row["ok"] is True
    assert row["verified_at"] is None


def test_the_fix_log_filters_on_host_action_and_outcome(client):
    tid, secret, _ = seeded(client)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    db = SessionLocal()
    try:
        db.add(m.Fix(tenant_id=tid, ts=now, host="a", action="enable_ufw", by="auto", ok=True, out=""))
        db.add(m.Fix(tenant_id=tid, ts=now, host="b", action="reboot", by="miro", ok=False, out="failed"))
        db.commit()
    finally:
        db.close()
    assert len(client.get("/v1/fixes?host=a", headers=bearer(secret)).json()["items"]) == 1
    assert len(client.get("/v1/fixes?ok=false", headers=bearer(secret)).json()["items"]) == 1
    assert len(client.get("/v1/fixes?by=miro", headers=bearer(secret)).json()["items"]) == 1
    assert len(client.get("/v1/fixes?action=reboot", headers=bearer(secret)).json()["items"]) == 1
