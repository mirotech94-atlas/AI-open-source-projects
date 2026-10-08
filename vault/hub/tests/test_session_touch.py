"""`session.last_seen` is not written on every request.

Every dashboard tab polls /live every four seconds and /overview alongside it.
Writing `last_seen` on each authenticated request therefore put fifteen to
thirty writes a minute, per open tab, on the read path of a SQLite database --
and once an hour a bulk background job held the write lock long enough that
those writes lost. The journal shows the result: `UPDATE session SET last_seen`
raising `database is locked`, and /overview, /inbox and /updates all returning
500 in the same second.

A longer `busy_timeout` makes that collision survivable. Not writing at all
makes it not happen.
"""

import datetime as dt

from guardian_hub import deps, models as m
from guardian_hub.db import SessionLocal
from tests.test_auth import login, seed_user


def _latest(db):
    return db.query(m.Session).order_by(m.Session.created.desc()).first()


def test_a_burst_of_requests_writes_last_seen_once(client):
    seed_user()
    login(client)
    client.get("/api/v1/me")

    db = SessionLocal()
    first = _latest(db).last_seen
    db.close()
    assert first is not None

    for _ in range(5):
        client.get("/api/v1/me")

    db = SessionLocal()
    again = _latest(db).last_seen
    db.close()
    assert again == first, "last_seen was rewritten inside the throttle window"


def test_last_seen_is_written_again_once_it_is_stale(client):
    seed_user()
    login(client)
    client.get("/api/v1/me")

    stale = dt.datetime.now(dt.timezone.utc) - deps._TOUCH_EVERY - dt.timedelta(seconds=5)
    db = SessionLocal()
    row = _latest(db)
    row.last_seen = stale
    db.commit()
    db.close()

    client.get("/api/v1/me")

    db = SessionLocal()
    written = _latest(db).last_seen
    db.close()
    if written.tzinfo is None:
        written = written.replace(tzinfo=dt.timezone.utc)
    assert written > stale, "a session that went stale was never refreshed"
