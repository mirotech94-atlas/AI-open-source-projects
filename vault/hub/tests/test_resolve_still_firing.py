"""Resolve has to admit when the thing is still happening.

Ack survives a re-fire: the sensor keeps an `acked` list and honours it. But
resolve is a claim about the world, so when the condition underneath is still
true the very next sweep raises the alert again -- correctly. From the outside
that is indistinguishable from the button not working, and the owner reported
it as exactly that: "I press resolve and it doesn't work".

The route now says which case it was, so the screen can tell the owner up
front instead of letting the row quietly reappear.
"""
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m


def _newest_scan_ts(db, tid):
    return (db.query(m.Scan.ts).filter(m.Scan.tenant_id == tid)
              .order_by(m.Scan.ts.desc()).limit(1).scalar())


def test_resolving_something_still_happening_says_so(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    ts = _newest_scan_ts(db, tid)
    a = db.query(m.Alert).filter_by(tenant_id=tid).first()
    a.last_seen = ts          # reported by the most recent sweep
    key = a.key
    db.commit()

    body = client.post(f"/api/v1/alerts/{key}/resolve").json()
    assert body["status"] == "resolved"
    assert body["still_firing"] is True


def test_resolving_something_that_has_stopped_does_not(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    a = db.query(m.Alert).filter_by(tenant_id=tid).first()
    a.last_seen = "2000-01-01T00:00:00+00:00"   # long gone
    key = a.key
    db.commit()

    body = client.post(f"/api/v1/alerts/{key}/resolve").json()
    assert body["status"] == "resolved"
    assert body["still_firing"] is False


def test_ack_reports_it_too_so_the_screen_can_choose_its_words(client):
    """The flag is about the condition, not about which button was pressed."""
    tid = seeded_demo(client)
    db = SessionLocal()
    ts = _newest_scan_ts(db, tid)
    a = db.query(m.Alert).filter_by(tenant_id=tid).first()
    a.last_seen = ts
    key = a.key
    db.commit()

    assert client.post(f"/api/v1/alerts/{key}/ack").json()["still_firing"] is True


def test_an_alert_with_no_last_seen_is_not_called_still_firing(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    a = db.query(m.Alert).filter_by(tenant_id=tid).first()
    a.last_seen = ""
    key = a.key
    db.commit()
    assert client.post(f"/api/v1/alerts/{key}/resolve").json()["still_firing"] is False
