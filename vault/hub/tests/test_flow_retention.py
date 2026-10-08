import datetime as dt
from guardian_hub.db import SessionLocal
from guardian_hub import models as m, retention
from tests.test_seed_helpers import seeded_demo

#: A mac no seeded row ever carries (`seed._gen_flows` only ever picks a mac
#: out of the fixture's own `last["lan"]["devices"]`, never a bare synthetic
#: one like this), so every `Flow`/`FlowRollup` assertion below can scope to
#: it and stay blind to the ~600 flow rows Task 14 now seeds for the demo
#: tenant on every `seeded_demo(client)` call.
MAC = "aa:bb:cc:ff:ff:01"

def _old_flow(db, tenant_id, days_ago, peer="1.1.1.1", name="", secs=0):
    # `uq_flow` is (tenant, ts, mac, peer_ip, peer_port, proto, source), so two
    # calls in the same wall-clock second collide. Anchor at noon on the target
    # day and space them with `secs`: the rows stay distinct, both still fall in
    # one `substr(ts, 1, 10)` bucket, and neither depends on when the suite runs
    # -- an offset from `now` can straddle midnight and split the day in two.
    day = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).date()
    ts = (dt.datetime.combine(day, dt.time(12), tzinfo=dt.timezone.utc)
          + dt.timedelta(seconds=secs)).isoformat(timespec="seconds")
    db.add(m.Flow(tenant_id=tenant_id, ts=ts, mac=MAC, ip="192.168.1.20", peer_ip=peer,
                  peer_port=443, proto="tcp", bytes_in=500, bytes_out=50, pkts_in=5, pkts_out=1, peer_name=name, hints={}))

def test_rollup_runs_before_raw_rows_age_out_and_answers_by_name(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    _old_flow(db, t.id, 10, name="www.google.com"); _old_flow(db, t.id, 10, name="www.google.com", secs=60); db.commit()
    counts = retention.prune_tenant(db, t)
    # `counts["flow"]`/`counts["flows_rolled_up"]` are already tenant-wide,
    # not row-scoped -- but they only count rows OLDER than `flow_raw_days`
    # (7), and Task 14's seeded flows are all inside the trailing 24h, so
    # they never enter this count. Only the bare `Flow`/`FlowRollup` queries
    # below need the `mac=MAC` scope, to stay blind to those ~600 fresh rows
    # still sitting in the table.
    assert counts["flows_rolled_up"] == 1 and counts["flow"] == 2
    roll = db.query(m.FlowRollup).filter_by(tenant_id=t.id, mac=MAC).one()
    assert roll.peer == "www.google.com" and roll.bytes_in == 1000 and roll.pkts == 12
    assert db.query(m.Flow).filter_by(tenant_id=t.id, mac=MAC).count() == 0

def test_fresh_rows_are_left_alone(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    _old_flow(db, t.id, 1); db.commit()
    retention.prune_tenant(db, t)
    assert db.query(m.Flow).filter_by(tenant_id=t.id, mac=MAC).count() == 1

def test_dry_run_leaves_flows_untouched_and_matches_the_real_run(client):
    """Dry run must report the same counts a real run would produce, and
    must not write or delete a single row while doing it -- the flow steps
    are a separate code path from the DNS ones `test_retention.py`'s own
    dry-run test covers, so that guarantee needs its own proof here."""
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    _old_flow(db, t.id, 10, name="www.google.com")
    _old_flow(db, t.id, 10, name="www.google.com", secs=60)
    _old_flow(db, t.id, 1)
    db.commit()

    dry = retention.prune_tenant(db, t, dry_run=True)
    assert db.query(m.Flow).filter_by(tenant_id=t.id, mac=MAC).count() == 3
    assert db.query(m.FlowRollup).filter_by(tenant_id=t.id, mac=MAC).count() == 0

    real = retention.prune_tenant(db, t)
    # Tenant-wide, but only over rows older than `flow_raw_days` -- Task 14's
    # seeded flows are all fresh, see the note in the first test above.
    assert dry["flows_rolled_up"] == real["flows_rolled_up"] == 1
    assert dry["flow"] == real["flow"] == 2
    assert dry["flow_rollup"] == real["flow_rollup"] == 0
    assert db.query(m.Flow).filter_by(tenant_id=t.id, mac=MAC).count() == 1
    assert db.query(m.FlowRollup).filter_by(tenant_id=t.id, mac=MAC).count() == 1

def test_rollup_peer_falls_back_to_ip_when_the_sensor_saw_no_name(client):
    """`peer` is `coalesce(nullif(peer_name, ''), peer_ip)` -- the existing
    rollup test only exercises the named branch. `peer_name` is a
    non-nullable `str` column (`default=""`), so there is no NULL case to
    add alongside this empty-string one."""
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    _old_flow(db, t.id, 10, peer="9.9.9.9", name="")
    _old_flow(db, t.id, 10, peer="9.9.9.9", name="", secs=60)
    db.commit()
    retention.prune_tenant(db, t)
    roll = db.query(m.FlowRollup).filter_by(tenant_id=t.id, mac=MAC).one()
    assert roll.peer == "9.9.9.9"
