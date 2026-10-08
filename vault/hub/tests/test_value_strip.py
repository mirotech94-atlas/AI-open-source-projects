"""The Reports value strip — the surface most likely to flatter.

Two of the four numbers the v2 design named cannot honestly be produced, and
these tests exist to stop them coming back: there is no measurement of how
long a problem lasted, and none at all of the owner's time.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import value as val
from tests.test_auth import seed_user


def _fix(tid, action="restart_service", by="auto", ok=True, ago=1):
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=ago)).isoformat(timespec="seconds")
    return m.Fix(tenant_id=tid, ts=ts, host="vps1", action=action, by=by, ok=ok)


def _run(tid, secs, ago=1):
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=ago)
    return m.Queue(tenant_id=tid, who="auto", path="/api/fix", body={}, status="done",
                   ts=start, started_at=start, finished_at=start + dt.timedelta(seconds=secs))


def _strip(tid, days=30):
    db = SessionLocal()
    try:
        return val.build(db, tid, days)
    finally:
        db.close()


def _stat(strip, key):
    return next(s for s in strip.stats if s.key == key)


def test_actions_counts_only_repairs_that_worked(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_fix(tid), _fix(tid), _fix(tid, ok=False)])
    db.commit(); db.close()
    assert _stat(_strip(tid), "actions").value == "2"


def test_handled_alone_is_a_share_of_a_named_total(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_fix(tid), _fix(tid), _fix(tid), _fix(tid, by="hub:miro")])
    db.commit(); db.close()
    s = _stat(_strip(tid), "alone")
    assert s.value == "75%"
    assert "3 of 4" in s.note, "a bare percentage hides how big the sample was"


def test_an_empty_window_does_not_report_a_hundred_percent(client):
    """No repairs is not "Vault handled everything"."""
    _uid, tid = seed_user()
    s = _stat(_strip(tid), "alone")
    assert s.value == "—"
    assert "nothing needed fixing" in s.note


def test_repair_time_is_named_for_what_it_measures(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_run(tid, 30), _run(tid, 90), _run(tid, 150)])
    db.commit(); db.close()
    s = _stat(_strip(tid), "runtime")
    assert s.label == "Median repair time"
    assert "once it started" in s.note
    # The claim it must never make: how long the problem lasted.
    assert "problem" not in s.note.lower()
    assert s.value == "90s"


def test_there_is_no_invented_time_saved(client):
    """The design asked for "your time spent". Vault cannot see the owner's
    clock, and minutes-saved from an approval count is a sales figure."""
    _uid, tid = seed_user()
    strip = _strip(tid)
    blob = " ".join(f"{s.key} {s.label} {s.value} {s.note}" for s in strip.stats).lower()
    for invented in ("time saved", "hours saved", "time spent", "we saved"):
        assert invented not in blob


def test_decisions_counts_what_was_actually_asked(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_fix(tid), _fix(tid, by="hub:miro"), _fix(tid, by="accepted-recommendation")])
    db.commit(); db.close()
    assert _stat(_strip(tid), "decisions").value == "2"


def test_work_is_grouped_by_outcome_not_by_command(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_fix(tid, "restart_service"), _fix(tid, "restart_container"),
                _fix(tid, "cleanup_disk"), _fix(tid, "enable_ufw")])
    db.commit(); db.close()
    work = {w.label: w.count for w in _strip(tid).work}
    assert work["Service restarts"] == 2
    assert work["Disk and log cleanup"] == 1
    assert work["Firewall and access"] == 1


def test_an_unknown_action_is_still_counted(client):
    """A total that does not match the rows above it is a bug report waiting
    to happen, so nothing is silently dropped."""
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_fix(tid, "some_future_action"), _fix(tid, "restart_service")])
    db.commit(); db.close()
    strip = _strip(tid)
    assert sum(w.count for w in strip.work) == int(_stat(strip, "actions").value)
    assert any(w.label == "Other repairs" for w in strip.work)


def test_old_rows_fall_out_of_the_window(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_fix(tid, ago=2), _fix(tid, ago=400)])
    db.commit(); db.close()
    assert _stat(_strip(tid, days=30), "actions").value == "1"
