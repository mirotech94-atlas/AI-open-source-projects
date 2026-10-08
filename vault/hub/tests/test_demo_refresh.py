"""`seed.refresh_demo` / `scheduler.demo_refresh_job` / `guardian demo-refresh`
(Task 64): keeps the `demo` tenant's window rolling so guarder.io's public
"Live from the demo network" panel (`routers/public_feed.py`) never shows a
newest row that is days stale.

Covers exactly the risks named in the brief:
- no-op with no demo tenant (a self-hosted hub that never ran `seed-demo`)
- never touches a second tenant present in the same database
- running it twice in a row leaves a consistent state
- the demo sign-in still works afterwards
- the public feed's newest row is recent after a run
"""
import datetime as dt

from guardian_hub import models as m, scheduler
from guardian_hub.config import settings
from guardian_hub.db import SessionLocal
from guardian_hub.routers import public_feed as pf
from guardian_hub.seed import refresh_demo, seed_demo

FEED = "/api/v1/public/feed"


def _demo_models():
    return {
        "device": m.Device, "metric": m.Metric, "event": m.Event,
        "dns": m.DnsQuery, "presence": m.PresenceSample, "fix": m.Fix,
        "scan": m.Scan, "site": m.Site,
    }


def _counts(db, tenant_id):
    return {name: db.query(model).filter_by(tenant_id=tenant_id).count()
            for name, model in _demo_models().items()}


# --------------------------------------------------------------------------
# no demo tenant -> no-op
# --------------------------------------------------------------------------

def test_no_demo_tenant_is_a_no_op(client):
    db = SessionLocal()
    assert db.query(m.Tenant).count() == 0
    out = refresh_demo(db)
    assert out is None
    assert db.query(m.Tenant).count() == 0


def test_scheduler_job_is_a_no_op_with_no_demo_tenant_even_when_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_refresh_enabled", True)
    scheduler.demo_refresh_job()   # must not raise, must create nothing
    db = SessionLocal()
    assert db.query(m.Tenant).count() == 0


def test_scheduler_job_is_a_no_op_when_disabled_even_with_a_demo_tenant(client, monkeypatch):
    db = SessionLocal()
    seed_demo(db)
    before = db.query(m.Event).filter_by(
        tenant_id=db.query(m.Tenant).filter_by(slug="demo").one().id
    ).count()
    monkeypatch.setattr(settings, "demo_refresh_enabled", False)
    scheduler.demo_refresh_job()
    after = db.query(m.Event).filter_by(
        tenant_id=db.query(m.Tenant).filter_by(slug="demo").one().id
    ).count()
    assert before == after


# --------------------------------------------------------------------------
# scope: never touches another tenant
# --------------------------------------------------------------------------

def test_never_touches_a_second_tenant(client):
    db = SessionLocal()
    seed_demo(db)
    other = m.Tenant(slug="acme", name="Acme Ltd")
    db.add(other); db.commit()
    db.add(m.Fix(tenant_id=other.id, ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                 host="acme-db", action="restart_service", label="acme fix", ok=True, by="auto"))
    db.add(m.DnsQuery(tenant_id=other.id, ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                      client="10.9.9.9", name="acme-internal.example", qtype="A", blocked=True, list="", ms=0))
    db.commit()
    other_before = _counts(db, other.id)
    other_before["fix"] = db.query(m.Fix).filter_by(tenant_id=other.id).count()
    other_before["dns"] = db.query(m.DnsQuery).filter_by(tenant_id=other.id).count()

    out = refresh_demo(db)
    assert out is not None

    db.expire_all()
    assert db.query(m.Tenant).filter_by(slug="acme").one().name == "Acme Ltd"
    after = _counts(db, other.id)
    after["fix"] = db.query(m.Fix).filter_by(tenant_id=other.id).count()
    after["dns"] = db.query(m.DnsQuery).filter_by(tenant_id=other.id).count()
    assert other_before == after
    assert db.query(m.Tenant).count() == 2


# --------------------------------------------------------------------------
# idempotency: running it twice in a row leaves a consistent state
# --------------------------------------------------------------------------

def test_running_twice_in_a_row_is_consistent(client):
    db = SessionLocal()
    seed_demo(db)
    tenant = db.query(m.Tenant).filter_by(slug="demo").one()

    out1 = refresh_demo(db)
    db.expire_all()
    counts1 = _counts(db, tenant.id)

    out2 = refresh_demo(db)
    db.expire_all()
    counts2 = _counts(db, tenant.id)

    assert out1 is not None and out2 is not None
    assert db.query(m.Tenant).filter_by(slug="demo").count() == 1
    # Same fixtures, same seeded RNG, same relative structure -> identical
    # row counts every time, regardless of how many times it has run.
    assert counts1 == counts2
    # Only ever one demo user/admin pair -- refresh_demo must never
    # duplicate accounts across repeated runs.
    assert db.query(m.User).filter(m.User.username.in_(["demo", "demoadmin"])).count() == 2
    assert db.query(m.Membership).filter_by(tenant_id=tenant.id).count() == 2
    # The fixed sign-in code is untouched across repeated refreshes.
    db.refresh(tenant)
    assert tenant.settings["demo_code"] == "424242"


# --------------------------------------------------------------------------
# demo sign-in still works afterwards
# --------------------------------------------------------------------------

def test_demo_sign_in_still_works_after_refresh(client):
    db = SessionLocal()
    seed_demo(db)
    refresh_demo(db)
    refresh_demo(db)   # and after a second run
    # Close before touching the API: this session's connection would otherwise
    # still hold an open SQLite transaction, and the request's own session
    # reads a snapshot taken before the seed committed.
    db.close()

    r = client.post("/api/v1/auth/verify", json={"username": "demoadmin", "code": "424242"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["username"] == "demoadmin"

    r2 = client.post("/api/v1/auth/verify", json={"username": "demo", "code": "424242"})
    assert r2.status_code == 200, r2.text


# --------------------------------------------------------------------------
# the public feed's newest row is recent after a run
# --------------------------------------------------------------------------

def test_public_feed_newest_row_is_recent_after_refresh(client):
    db = SessionLocal()
    seed_demo(db)

    # Simulate the "stale shop window" the brief describes: back-date the
    # freshly-seeded tenant by re-seeding it anchored days in the past.
    stale_anchor = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=4)
    tenant = db.query(m.Tenant).filter_by(slug="demo").one()
    from guardian_hub.seed import reset_tenant
    reset_tenant(db, tenant.id)
    seed_demo(db, anchor=stale_anchor)
    pf.reset_cache()

    stale_rows = client.get(FEED).json()["rows"]
    stale_newest = max(dt.datetime.fromisoformat(r["ts"]) for r in stale_rows)
    assert dt.datetime.now(dt.timezone.utc) - stale_newest > dt.timedelta(hours=12)

    out = refresh_demo(db)
    assert out is not None
    pf.reset_cache()

    fresh_rows = client.get(FEED).json()["rows"]
    assert fresh_rows
    fresh_newest = max(dt.datetime.fromisoformat(r["ts"]) for r in fresh_rows)
    assert dt.datetime.now(dt.timezone.utc) - fresh_newest < dt.timedelta(minutes=10)


def test_cli_demo_refresh_subcommand(client, capsys):
    from guardian_hub.cli import main as cli_main

    # No tenant yet -> reports skipped rather than erroring or creating one.
    cli_main(["demo-refresh"])
    out = capsys.readouterr().out
    assert '"skipped": true' in out.lower()

    db = SessionLocal()
    seed_demo(db)
    cli_main(["demo-refresh"])
    out = capsys.readouterr().out
    assert '"tenant_id"' in out


def test_refresh_does_not_orphan_the_demos_traffic_flows(client):
    """Flows are rebuilt with the rest of the tenant, not left behind.

    `reset_tenant` deletes and recreates the demo's `Network` rows, so every
    re-seed mints new network ids -- and `Flow` was not on its list. Each
    refresh therefore left the previous run's flows pointing at networks that
    no longer existed and added a fresh set beside them. The refresh runs
    every ten minutes; on production that had grown to 405,479 orphaned rows
    against 430 live ones (99.9%), invisible on every per-network Traffic view,
    scanned by every all-networks one, and a large share of the database
    doubling in two days. `test_running_twice_in_a_row_is_consistent` stayed
    green throughout because its `_counts` never looked at flows.
    """
    db = SessionLocal()
    seed_demo(db)
    tenant = db.query(m.Tenant).filter_by(slug="demo").one()

    refresh_demo(db)
    db.expire_all()
    first = db.query(m.Flow).filter_by(tenant_id=tenant.id).count()

    refresh_demo(db)
    db.expire_all()
    second = db.query(m.Flow).filter_by(tenant_id=tenant.id).count()

    live = {n.id for n in db.query(m.Network).filter_by(tenant_id=tenant.id)}
    orphaned = (db.query(m.Flow).filter(m.Flow.tenant_id == tenant.id,
                                        m.Flow.network_id.notin_(live)).count())
    assert first > 0, "the demo seeds no flows, so this test proves nothing"
    assert second == first, f"flows grew from {first} to {second} across one refresh"
    assert orphaned == 0, f"{orphaned} flows point at a network the refresh deleted"
