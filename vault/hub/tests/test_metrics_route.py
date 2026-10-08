"""`GET /metrics` answers its window, in any offset, without reading the
tenant's whole metric history (it loaded every row: 7.7 s on the production
home network, 2026-10-06)."""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def _metric(db, tid, hours_ago, value, offset_h=0):
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)).replace(microsecond=0)
    when = when.astimezone(dt.timezone(dt.timedelta(hours=offset_h)))
    db.add(m.Metric(tenant_id=tid, ts=when.isoformat(), name="probe:test", entity="e1", value=value))


def test_metrics_returns_the_window_in_any_offset_and_nothing_older(client):
    seeded_demo(client)
    db = SessionLocal()
    tid = db.query(m.Tenant).filter_by(slug="demo").one().id
    _metric(db, tid, 72, 1.0)                  # outside 24 h
    _metric(db, tid, 23, 2.0, offset_h=-10)    # inside, written far west: sorts EARLY as text
    _metric(db, tid, 1, 3.0, offset_h=5)
    db.commit(); db.close()
    got = client.get("/api/v1/metrics?name=probe:test&hours=24").json()
    assert sorted(r["value"] for r in got) == [2.0, 3.0]
