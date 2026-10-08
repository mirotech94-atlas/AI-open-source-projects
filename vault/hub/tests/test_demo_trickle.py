"""`demo_trickle.trickle_demo` -- the between-refresh rows that keep
guarder.io's public feed panel (`routers/public_feed.py`) showing a newest
row that is seconds old, not the up-to-ten-minutes-stale rows
`scheduler.demo_refresh_job`'s own cadence leaves behind (see that module's
docstring, "The gap this fills", for the full reasoning: even right after a
refresh, only `DnsQuery` rows land within seconds of the anchor -- `Event`
and `Fix` do not).

Covers exactly the risks named in the brief:
- the newest row is seconds old, not minutes, after a tick
- retention actually bounds growth (not "the next refresh happens to wipe it")
- a tick never touches a second tenant
- the scheduler's own cadence constant cannot silently drift from this module's
"""
import datetime as dt

from sqlalchemy import func

from guardian_hub import demo_trickle, models as m, scheduler
from guardian_hub.db import SessionLocal
from guardian_hub.seed import seed_demo

from tests import scanfixture


def _seed(db):
    return seed_demo(db, fixtures=scanfixture.rebased_dir())


# --------------------------------------------------------------------------
# the scheduler's cadence constant tracks this module's, always
# --------------------------------------------------------------------------

def test_scheduler_interval_matches_this_modules_constant():
    assert scheduler.DEMO_TRICKLE_INTERVAL_S == demo_trickle.TRICKLE_INTERVAL_S


# --------------------------------------------------------------------------
# no demo tenant -> no-op, same contract as refresh_demo
# --------------------------------------------------------------------------

def test_no_demo_tenant_is_a_no_op(client):
    db = SessionLocal()
    assert demo_trickle.trickle_demo(db) is None
    assert db.query(m.Event).count() == 0
    assert db.query(m.Fix).count() == 0
    assert db.query(m.DnsQuery).count() == 0


# --------------------------------------------------------------------------
# the newest row is seconds old after a tick, not minutes
# --------------------------------------------------------------------------

def test_a_tick_leaves_a_seconds_old_row(client):
    db = SessionLocal()
    _seed(db)

    out = demo_trickle.trickle_demo(db)
    assert out is not None and out["tenant_id"]

    newest = {
        "event": db.query(m.Event).filter_by(tenant_id=out["tenant_id"]).order_by(m.Event.ts.desc()).first(),
        "fix": db.query(m.Fix).filter_by(tenant_id=out["tenant_id"]).order_by(m.Fix.ts.desc()).first(),
        "dns": db.query(m.DnsQuery).filter_by(tenant_id=out["tenant_id"]).order_by(m.DnsQuery.ts.desc()).first(),
    }
    row = newest[out["kind"]]
    assert row is not None
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(row.ts.replace("Z", "+00:00"))
    assert age < dt.timedelta(seconds=30), f"trickled {out['kind']} row is {age} old"


def test_several_ticks_keep_the_newest_row_seconds_old(client):
    """Round-robins through dns/port/fix -- after enough ticks to have hit
    every kind at least once, the single newest row across all three tables
    (what `public_feed.build_feed` actually sorts on) must still be fresh."""
    db = SessionLocal()
    tenant_id = _seed(db)["tenant_id"]

    for _ in range(6):
        demo_trickle.trickle_demo(db)

    stamps = []
    for model in (m.Event, m.Fix, m.DnsQuery):
        row = db.query(model).filter_by(tenant_id=tenant_id).order_by(model.ts.desc()).first()
        if row:
            stamps.append(dt.datetime.fromisoformat(row.ts.replace("Z", "+00:00")))
    newest = max(stamps)
    assert dt.datetime.now(dt.timezone.utc) - newest < dt.timedelta(seconds=30)


# --------------------------------------------------------------------------
# retention actually bounds growth
# --------------------------------------------------------------------------

def test_retention_bounds_the_trickled_rows_not_just_the_next_refresh(client):
    """Simulate an hour of ticks (via the `now` seam, no real sleeping) and
    confirm the count of rows this module has added stays bounded -- it must
    not rely on `demo_refresh_job`'s ten-minute reset to do this for it."""
    db = SessionLocal()
    tenant_id = _seed(db)["tenant_id"]
    before = {
        "event": db.query(m.Event).filter_by(tenant_id=tenant_id).count(),
        "fix": db.query(m.Fix).filter_by(tenant_id=tenant_id).count(),
        "dns": db.query(m.DnsQuery).filter_by(tenant_id=tenant_id).count(),
        "flow": db.query(m.Flow).filter_by(tenant_id=tenant_id).count(),
    }

    start = dt.datetime.now(dt.timezone.utc)
    ticks = 400  # far more than 3600s / TRICKLE_INTERVAL_S would ever run in an hour
    for i in range(ticks):
        demo_trickle.trickle_demo(db, now=start + dt.timedelta(seconds=i * demo_trickle.TRICKLE_INTERVAL_S))

    after = {
        "event": db.query(m.Event).filter_by(tenant_id=tenant_id).count(),
        "fix": db.query(m.Fix).filter_by(tenant_id=tenant_id).count(),
        "dns": db.query(m.DnsQuery).filter_by(tenant_id=tenant_id).count(),
        "flow": db.query(m.Flow).filter_by(tenant_id=tenant_id).count(),
    }
    added = {k: after[k] - before[k] for k in before}
    # Steady state per `demo_trickle`'s own retention note: at most
    # TRICKLE_KEEP_S / TRICKLE_INTERVAL_S rows outstanding across all three
    # event/fix/dns tables combined, not "however many ticks have ever run".
    dns_cap = demo_trickle.TRICKLE_KEEP_S / demo_trickle.TRICKLE_INTERVAL_S + len(demo_trickle._KINDS)
    # `_add_flows` runs every tick too, but its rows are stamped at the
    # trickled MINUTE (`_add_flows`'s own docstring) and de-duped against
    # whatever already exists at that minute -- so of the four 15s ticks
    # that share one wall-clock minute, only the FIRST actually inserts (up
    # to 4 rows, 2 devices x 2 peers); the other three are no-ops. Flow rows
    # therefore accumulate at up to 4 rows per 60s of wall-clock time, not
    # per tick, so the outstanding count after one `TRICKLE_KEEP_S` window
    # is `4 * (TRICKLE_KEEP_S / 60)`, plus one more minute's worth of slack
    # for the boundary minute still inside the window when it is measured.
    flow_cap = 4 * (demo_trickle.TRICKLE_KEEP_S / 60) + 4
    assert sum(added.values()) <= dns_cap + flow_cap, added

    # And the newest row is still anchored at the simulated "now", not stuck
    # at the first tick -- pruning must not have thrown away live rows.
    last_tick_ts = (start + dt.timedelta(seconds=(ticks - 1) * demo_trickle.TRICKLE_INTERVAL_S))
    stamps = []
    for model in (m.Event, m.Fix, m.DnsQuery):
        row = db.query(model).filter_by(tenant_id=tenant_id).order_by(model.ts.desc()).first()
        if row:
            stamps.append(dt.datetime.fromisoformat(row.ts.replace("Z", "+00:00")))
    assert max(stamps) >= last_tick_ts - dt.timedelta(seconds=demo_trickle.TRICKLE_INTERVAL_S)


# --------------------------------------------------------------------------
# never touches a second tenant
# --------------------------------------------------------------------------

def test_never_touches_a_second_tenant(client):
    db = SessionLocal()
    _seed(db)
    other = m.Tenant(slug="acme", name="Acme Ltd")
    db.add(other)
    db.commit()
    before = {
        "event": db.query(m.Event).filter_by(tenant_id=other.id).count(),
        "fix": db.query(m.Fix).filter_by(tenant_id=other.id).count(),
        "dns": db.query(m.DnsQuery).filter_by(tenant_id=other.id).count(),
        "flow": db.query(m.Flow).filter_by(tenant_id=other.id).count(),
    }

    for _ in range(5):
        demo_trickle.trickle_demo(db)

    after = {
        "event": db.query(m.Event).filter_by(tenant_id=other.id).count(),
        "fix": db.query(m.Fix).filter_by(tenant_id=other.id).count(),
        "dns": db.query(m.DnsQuery).filter_by(tenant_id=other.id).count(),
        "flow": db.query(m.Flow).filter_by(tenant_id=other.id).count(),
    }
    assert before == after


# --------------------------------------------------------------------------
# a tick's flow rows never invent a third peer
# --------------------------------------------------------------------------

def test_trickle_adds_flows_for_only_the_two_named_peers(client):
    db = SessionLocal()
    tenant_id = _seed(db)["tenant_id"]
    max_id_before = db.query(func.max(m.Flow.id)).filter_by(tenant_id=tenant_id).scalar() or 0

    out = demo_trickle.trickle_demo(db)
    assert out is not None

    new_rows = (
        db.query(m.Flow)
        .filter(m.Flow.tenant_id == tenant_id, m.Flow.id > max_id_before)
        .all()
    )
    assert len(new_rows) == 4
    assert {r.peer_name for r in new_rows} == {"www.google.com", "dentrix.example"}
    assert all(r.source == "mirror" for r in new_rows)
    assert all(r.hints == {} for r in new_rows)


# --------------------------------------------------------------------------
# forget_tenant / the scheduler wiring around a refresh
# --------------------------------------------------------------------------

def test_forget_tenant_lets_a_reset_rows_id_be_reused_safely(client, monkeypatch):
    """After `forget_tenant`, a stale id this module was tracking is no
    longer retried against the database -- exercised indirectly by driving
    `demo_refresh_job` (which calls `forget_tenant` itself) right after some
    ticks, and confirming nothing raises and the tenant is left consistent."""
    from guardian_hub.config import settings

    db = SessionLocal()
    _seed(db)
    db.close()

    monkeypatch.setattr(settings, "demo_refresh_enabled", True)
    scheduler.demo_trickle_job()
    scheduler.demo_trickle_job()
    scheduler.demo_refresh_job()   # resets + reseeds; must also call forget_tenant
    scheduler.demo_trickle_job()   # must not raise against stale ids

    db2 = SessionLocal()
    tenant = db2.query(m.Tenant).filter_by(slug="demo").one()
    assert db2.query(m.Scan).filter_by(tenant_id=tenant.id).count() > 0


# --------------------------------------------------------------------------
# the heartbeat scan: what lets the full re-seed run hourly, not every 10 min
# --------------------------------------------------------------------------

def _newest_scan(db, tid):
    from guardian_hub.scans import latest_scan, parse_ts
    s = latest_scan(db, tid)
    return s, parse_ts(s.ts)


def test_a_tick_writes_a_fresh_scan_once_the_newest_is_stale_enough(client):
    """The demo reads "online" only while its newest scan is under 15 minutes
    old. That used to be the ten-minute full re-seed's whole reason for its
    cadence -- a wipe and rebuild costing ~30 s of the production hub's only
    CPU, with the write lock held, six times an hour. A copied scan stamped
    now does the same job for one row insert."""
    db = SessionLocal()
    _seed(db)
    tid = db.query(m.Tenant).filter_by(slug="demo").one().id
    _, seen = _newest_scan(db, tid)

    later = seen + dt.timedelta(seconds=demo_trickle.HEARTBEAT_AFTER_S + 60)
    demo_trickle.trickle_demo(db, now=later)
    scan, when = _newest_scan(db, tid)
    assert abs((when - later).total_seconds()) < 2, "no fresh scan after the heartbeat interval"
    assert (scan.raw or {}).get("ts") == scan.ts, "the copied payload must carry its own new time"


def test_no_heartbeat_scan_while_the_newest_is_still_fresh(client):
    db = SessionLocal()
    _seed(db)
    tid = db.query(m.Tenant).filter_by(slug="demo").one().id
    before = db.query(m.Scan).filter_by(tenant_id=tid).count()
    _, seen = _newest_scan(db, tid)
    demo_trickle.trickle_demo(db, now=seen + dt.timedelta(seconds=30))
    assert db.query(m.Scan).filter_by(tenant_id=tid).count() == before


def test_the_full_reseed_runs_once_a_day():
    trigger = scheduler.DEMO_REFRESH_CRON
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["minute"] == "7" and fields["hour"] == "4", fields


def test_the_heartbeat_keeps_online_devices_online(client):
    """A device reads ONLINE only while its `last_seen` is under 15 minutes
    old (`effective_presence`). The heartbeat restamped the scan but not the
    devices, so with the full re-seed moved to hourly every demo device read
    UNKNOWN for 45 minutes of each hour -- 29 of them on production. The
    copied scan reports them present, so their `last_seen` moves with it."""
    from guardian_hub.payloads._util import effective_presence
    db = SessionLocal()
    _seed(db)
    tid = db.query(m.Tenant).filter_by(slug="demo").one().id
    online = [d.mac for d in db.query(m.Device).filter_by(tenant_id=tid) if d.presence == "ONLINE"]
    assert online, "the demo seeds no online devices, so this proves nothing"
    _, seen = _newest_scan(db, tid)
    later = seen + dt.timedelta(minutes=40)
    demo_trickle.trickle_demo(db, now=later)
    db.expire_all()
    from unittest import mock
    with mock.patch("guardian_hub.payloads._util._now", return_value=later):
        states = {d.mac: effective_presence(d) for d in db.query(m.Device).filter(
            m.Device.tenant_id == tid, m.Device.mac.in_(online))}
    assert set(states.values()) == {"ONLINE"}, states
