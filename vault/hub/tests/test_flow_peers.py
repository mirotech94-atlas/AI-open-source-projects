"""`FlowPeer`: when each destination was first and last seen, so "is this
peer new?" is a lookup of the peers on screen instead of a scan of a week of
raw flows. Measured on the production home network 2026-10-06: the scan was
3.9 million rows and 70-76 seconds per Live connections or Topology load.
"""
import datetime as dt
import re

from sqlalchemy import event
from sqlalchemy.engine import Engine

from guardian_hub import flowpeers, retention
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import traffic
from tests.test_seed_helpers import seeded_demo


def _tenant():
    db = SessionLocal()
    return db, db.query(m.Tenant).filter_by(slug="demo").one()


def _flow(db, t, peer_ip, minutes_ago, name="", hours_offset=0):
    when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)
    when = when.replace(microsecond=0).astimezone(dt.timezone(dt.timedelta(hours=hours_offset)))
    db.add(m.Flow(tenant_id=t.id, ts=when.isoformat(), mac="aa:bb:cc:00:00:44", ip="192.168.1.44",
                  peer_ip=peer_ip, peer_port=443, proto="tcp", bytes_in=1, bytes_out=1,
                  pkts_in=1, pkts_out=1, peer_name=name, hints={}))


def test_a_new_flow_records_its_peer_in_utc(client):
    seeded_demo(client); db, t = _tenant()
    _flow(db, t, "203.0.113.50", minutes_ago=10, hours_offset=5); db.commit()
    row = db.query(m.FlowPeer).filter_by(tenant_id=t.id, label="203.0.113.50").one()
    assert row.first_seen.endswith("+00:00") and row.first_seen == row.last_seen


def test_first_and_last_seen_widen_whatever_order_rows_arrive_in(client):
    seeded_demo(client); db, t = _tenant()
    _flow(db, t, "203.0.113.51", minutes_ago=5); db.commit()
    _flow(db, t, "203.0.113.51", minutes_ago=600); db.commit()     # a late, older row
    _flow(db, t, "203.0.113.51", minutes_ago=1); db.commit()
    row = db.query(m.FlowPeer).filter_by(tenant_id=t.id, label="203.0.113.51").one()
    first = dt.datetime.fromisoformat(row.first_seen); last = dt.datetime.fromisoformat(row.last_seen)
    now = dt.datetime.now(dt.timezone.utc)
    assert 590 <= (now - first).total_seconds() / 60 <= 610
    assert (now - last).total_seconds() < 180


def test_the_name_is_the_label_when_the_sensor_saw_one(client):
    seeded_demo(client); db, t = _tenant()
    _flow(db, t, "203.0.113.52", minutes_ago=5, name="api.example.com"); db.commit()
    assert db.query(m.FlowPeer).filter_by(tenant_id=t.id, label="api.example.com").count() == 1


def test_a_peer_written_in_another_offset_before_the_window_is_not_new(client):
    seeded_demo(client); db, t = _tenant()
    # Three hours before the window, written at +05:00: as TEXT that row reads
    # later than a +00:00 cutoff, which is the lexicographic trap.
    _flow(db, t, "203.0.113.53", minutes_ago=240, hours_offset=5)
    _flow(db, t, "203.0.113.53", minutes_ago=5); db.commit()
    assert "203.0.113.53" not in [p.peer for p in traffic.overview(db, t, hours=1).new_peers]


def test_seen_before_never_reads_the_raw_flow_table(client):
    seeded_demo(client); db, t = _tenant()
    _flow(db, t, "203.0.113.54", minutes_ago=5); db.commit()
    seen = []

    def grab(conn, cursor, statement, params, context, executemany):
        seen.append(statement.lower())

    event.listen(Engine, "before_cursor_execute", grab)
    try:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
        traffic._seen_before(db, t, None, cutoff - dt.timedelta(days=7), cutoff, {"203.0.113.54"})
    finally:
        event.remove(Engine, "before_cursor_execute", grab)
    assert seen, "the check ran no SQL at all -- the test proves nothing"
    assert not any(re.search(r"\bfrom flow\b(?!_)", s) for s in seen), seen


def test_a_peer_gone_longer_than_raw_retention_is_new_again(client):
    seeded_demo(client); db, t = _tenant()
    _flow(db, t, "203.0.113.55", minutes_ago=5); db.commit()
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    row = db.query(m.FlowPeer).filter_by(tenant_id=t.id, label="203.0.113.55").one()
    row.first_seen = row.last_seen = old; db.commit()
    retention.prune_tenant(db, t)
    assert db.query(m.FlowPeer).filter_by(tenant_id=t.id, label="203.0.113.55").count() == 0


def test_the_migration_backfills_first_and_last_seen_in_utc(tmp_path):
    """0035 reads the existing week of flows once. Two offsets for the same
    peer: as text, the +05:00 row sorts after the -04:00 one, but it is the
    EARLIER instant, and first_seen must say so. Run through the alembic CLI
    in a subprocess for the reasons `test_onboarding`'s 0011 test gives."""
    import os, pathlib, sqlite3, subprocess, sys
    repo = pathlib.Path(__file__).resolve().parents[2]
    db_path = tmp_path / "m.db"
    env = dict(os.environ, GUARDIAN_DATABASE_URL=f"sqlite:///{db_path}")
    env.setdefault("GUARDIAN_SECRET_KEY", "test-secret")

    def alembic(target):
        r = subprocess.run([sys.executable, "-m", "alembic", "-c", "hub/alembic.ini", "upgrade", target],
                           cwd=repo, env=env, capture_output=True, text=True)
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"

    alembic("0034_session_via_demo")
    c = sqlite3.connect(db_path)
    for ts in ("2026-10-01T10:00:00-04:00",      # 14:00Z
               "2026-10-01T18:30:00+05:00"):     # 13:30Z -- earlier, sorts later as text
        c.execute("INSERT INTO flow (tenant_id, network_id, ts, mac, ip, peer_ip, peer_port, proto, bytes_in, "
                  "bytes_out, pkts_in, pkts_out, peer_name, hints, source) VALUES ('t1', NULL, ?, "
                  "'aa:bb:cc:00:00:01', '192.168.1.2', '198.51.100.9', 443, 'tcp', 1, 1, 1, 1, '', '{}', 'mirror')",
                  (ts,))
    c.commit(); c.close()
    alembic("0035_flow_peer")
    c = sqlite3.connect(db_path)
    row = c.execute("SELECT network_id, label, first_seen, last_seen FROM flow_peer").fetchone()
    c.close()
    assert row == ("", "198.51.100.9", "2026-10-01T13:30:00+00:00", "2026-10-01T14:00:00+00:00")
