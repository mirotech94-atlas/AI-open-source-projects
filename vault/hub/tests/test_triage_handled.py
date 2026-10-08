"""`Handled for you` must count what Vault actually handled.

It read 0 on a hub with 139 successful repairs: it tested `by == "auto"`
(excluding every other stamp the sensor writes) and required `verified_at`,
which `verification.py` can only set when the host is in the latest scan and
the action has a known re-check — 5 rows out of 139.
"""
import datetime as dt

from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.payloads import triage
from tests.test_auth import seed_user


def _fix(db, tid, by, *, ok=True, verified=False, days_ago=1):
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago))
    db.add(m.Fix(tenant_id=tid, host="vps1", action="restart-service", label="x", arg="",
                 by=by, ok=ok, out="", reboot_needed=False, ts=ts.isoformat(timespec="seconds"),
                 verified_at=ts if verified else None))


def _count(tid):
    db = SessionLocal()
    try:
        return triage._handled_count(db, tid, dt.datetime.now(dt.timezone.utc))
    finally:
        db.close()


def test_an_unverified_automatic_fix_still_counts(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    _fix(db, tid, "auto", verified=False)
    _fix(db, tid, "auto", verified=True)
    db.commit(); db.close()
    assert _count(tid) == 2, "verification is a stronger claim, not the bar for 'handled'"


def test_a_stamp_that_is_not_the_word_auto_still_counts(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    for by in ("auto", "test", "sensor-7", "accepted-recommendation"):
        _fix(db, tid, by)
    db.commit(); db.close()
    # accepted-recommendation is a person's yes; the other three are Vault's.
    assert _count(tid) == 3


def test_a_persons_fix_and_a_failed_fix_are_not_handled_for_you(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    _fix(db, tid, "hub:miro")
    _fix(db, tid, "auto", ok=False)
    _fix(db, tid, "auto")
    db.commit(); db.close()
    assert _count(tid) == 1


def test_a_fix_older_than_the_window_falls_out(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    _fix(db, tid, "auto", days_ago=triage.HANDLED_WINDOW_DAYS + 3)
    _fix(db, tid, "auto", days_ago=1)
    db.commit(); db.close()
    assert _count(tid) == 1
