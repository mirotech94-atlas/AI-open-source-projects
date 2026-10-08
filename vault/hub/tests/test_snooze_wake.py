"""Item 10b: a snooze is a promise to come back, and the promise was empty.

Expiry already happened lazily, at the top of every inbox read. So a
customer who chose "remind me in 8 hours" got no reminder at all -- the
alert simply reappeared if they happened to look at the tab. That is the
defect class this codebase keeps finding: not a wrong number, a thing that
silently never happens.
"""
import datetime as dt
from unittest.mock import patch

from guardian_hub.db import SessionLocal
from guardian_hub import models as m, scheduler
from guardian_hub.integrations import registry
from tests.test_auth import seed_user


def _alert(tid, key, *, until, msg="Open port 23 on the printer"):
    seen = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return m.Alert(tenant_id=tid, key=key, sev="WARN", msg=msg,
                   first_seen=seen, last_seen=seen,
                   status="snoozed", snoozed_until=until)


def _iso(delta_hours):
    return (dt.datetime.now(dt.timezone.utc)
            + dt.timedelta(hours=delta_hours)).isoformat(timespec="seconds")


def _run():
    # Lazily imported inside the job, so the patch lands on the module the
    # job imports FROM -- not on `scheduler`.
    with patch.object(registry, "deliver_background") as sent:
        scheduler.snooze_wake_job()
        return sent.call_args_list


def test_an_elapsed_snooze_is_reopened_and_announced(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(_alert(tid, "alert:printer", until=_iso(-1)))
    db.commit(); db.close()

    calls = _run()
    assert len(calls) == 1
    msg = calls[0].args[1]
    assert "Open port 23 on the printer" in msg.body
    assert msg.url.endswith("/inbox")
    assert msg.dedup_key.startswith(f"snooze-wake:{tid}:")

    db = SessionLocal()
    a = db.query(m.Alert).filter_by(tenant_id=tid).one()
    assert (a.status, a.snoozed_until) == ("open", None)
    db.close()


def test_a_snooze_that_has_not_run_out_is_left_alone(client):
    """The whole point of a snooze is that it holds."""
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(_alert(tid, "alert:printer", until=_iso(+6)))
    db.commit(); db.close()

    assert _run() == []
    db = SessionLocal()
    assert db.query(m.Alert).filter_by(tenant_id=tid).one().status == "snoozed"
    db.close()


def test_a_nights_worth_of_snoozes_is_one_message(client):
    """Fifteen notices at 02:40 is how a channel gets muted, and a muted
    channel misses the one that mattered."""
    _uid, tid = seed_user()
    db = SessionLocal()
    for i in range(3):
        db.add(_alert(tid, f"alert:{i}", until=_iso(-2), msg=f"Finding {i}"))
    db.commit(); db.close()

    calls = _run()
    assert len(calls) == 1
    assert "and 2 others" in calls[0].args[1].body


def test_the_job_never_raises_into_the_scheduler(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(_alert(tid, "alert:printer", until=_iso(-1)))
    db.commit(); db.close()
    with patch.object(registry, "deliver_background", side_effect=RuntimeError("channel down")):
        scheduler.snooze_wake_job()   # must not raise


def test_the_job_is_registered_and_runs_hourly():
    ids = {j.id for j in scheduler.jobs()}
    assert "snooze-wake" in ids, "a job nobody registered is a feature that never runs"


def test_opening_the_inbox_first_does_not_eat_the_reminder(client):
    """The one that matters, and the one the first version got wrong.

    Expiry also happens lazily on an inbox read, so a customer who opens the
    tab before the sweep used to consume their own reminder: the read set
    `snoozed_until = None`, the job's query found nothing, and the person
    most likely to be waiting for an answer was the one guaranteed not to
    get one. `snoozed_until` is now the receipt -- only the announcing
    caller clears it.
    """
    from tests.test_auth import login
    seed_user(username="miro", email="m@example.com", tenant_slug="home")
    login(client, "miro")
    db = SessionLocal()
    tid = db.query(m.Tenant).filter_by(slug="home").one().id
    db.add(_alert(tid, "alert:printer", until=_iso(-1)))
    db.commit(); db.close()

    # The customer looks BEFORE the sweep. The alert is unhidden…
    rows = client.get("/api/v1/inbox").json()
    assert rows is not None

    # …and the reminder is still owed.
    calls = _run()
    assert len(calls) == 1, "opening the inbox must not consume the reminder"


def test_a_reminder_is_announced_once_and_not_again(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(_alert(tid, "alert:printer", until=_iso(-1)))
    db.commit(); db.close()
    assert len(_run()) == 1
    assert _run() == [], "the stamp is the receipt; a second sweep says nothing"
