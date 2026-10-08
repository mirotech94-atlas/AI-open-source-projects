import datetime as dt

from guardian_hub import models as m

def test_tables_exist(client):
    from guardian_hub.db import engine
    from sqlalchemy import inspect
    names = set(inspect(engine).get_table_names())
    for t in ["tenant","user","membership","otp","session","sensor","site","host","device","scan",
              "metric","event","alert","incident","recommendation","fix","dns_query","dns_rule",
              "edit","report","integration","audit","heartbeat","queue"]:
        assert t in names, t

def test_tzdatetime_roundtrips_aware(client):
    # SQLite has no native tz-aware storage: a naive DateTime(timezone=True)
    # column comes back naive on read-back in a fresh session. TZDateTime
    # (hub/guardian_hub/db.py) must normalize this so an aware value written
    # here comes back aware (and equal) when read in a brand-new SessionLocal.
    from guardian_hub.db import SessionLocal

    db = SessionLocal()
    u = m.User(username="tzuser", email="tz@example.com")
    db.add(u); db.flush()
    expires = dt.datetime(2030, 1, 1, 12, 30, tzinfo=dt.timezone.utc)
    s = m.Session(id="tz-roundtrip-token", user_id=u.id, expires=expires)
    db.add(s); db.commit()
    db.close()

    db2 = SessionLocal()
    got = db2.get(m.Session, "tz-roundtrip-token")
    assert got.expires.tzinfo is not None
    assert got.expires == expires
    # The real failure mode: deps.current_session / auth.verify compare the
    # read-back value against "now". A naive read-back raises
    # TypeError: can't compare offset-naive and offset-aware datetimes.
    now = dt.datetime.now(dt.timezone.utc)
    assert got.expires > now
    assert not (got.expires < now)
    db2.close()


def test_tzdatetime_nullable_columns_roundtrip_aware(client):
    # Same guarantee for the nullable timestamps future tasks will touch
    # (Sensor.enrol_expires, Heartbeat.last_ping, Report.share_expires):
    # None stays None, an aware value comes back aware and comparable.
    from guardian_hub.db import SessionLocal

    db = SessionLocal()
    t = m.Tenant(slug="tz-t", name="TZ")
    db.add(t); db.flush()
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
    db.add(m.Sensor(id="tz-sensor", tenant_id=t.id, enrol_expires=past))
    db.add(m.Heartbeat(id="tz-hb", tenant_id=t.id, name="hb", token="tok", last_ping=None))
    db.commit(); db.close()

    db2 = SessionLocal()
    sensor = db2.get(m.Sensor, "tz-sensor")
    hb = db2.get(m.Heartbeat, "tz-hb")
    assert sensor.enrol_expires.tzinfo is not None
    assert sensor.enrol_expires < dt.datetime.now(dt.timezone.utc)
    assert hb.last_ping is None
    db2.close()
