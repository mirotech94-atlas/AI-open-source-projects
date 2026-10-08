import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.scans import latest_scan, parse_ts


def test_parse_ts_handles_offsets_and_z():
    assert parse_ts("2026-09-09T12:00:00-04:00") == dt.datetime(2026, 9, 9, 16, 0, 0, tzinfo=dt.timezone.utc)
    assert parse_ts("2026-09-09T12:00:00+00:00") == dt.datetime(2026, 9, 9, 12, 0, 0, tzinfo=dt.timezone.utc)
    assert parse_ts("2026-09-09T12:00:00Z") == dt.datetime(2026, 9, 9, 12, 0, 0, tzinfo=dt.timezone.utc)
    # naive -> assumed UTC
    assert parse_ts("2026-09-09T12:00:00") == dt.datetime(2026, 9, 9, 12, 0, 0, tzinfo=dt.timezone.utc)


def test_parse_ts_returns_none_for_garbage():
    assert parse_ts("") is None
    assert parse_ts(None) is None
    assert parse_ts("not-a-timestamp") is None


def test_latest_scan_picks_chronological_max_not_string_max(client):
    db = SessionLocal()
    t = m.Tenant(slug="offsets", name="Offsets Co")
    db.add(t)
    db.commit()

    # "2026-09-09T23:00:00-04:00" (= 2026-09-10T03:00 UTC) sorts as a LARGER
    # string than "2026-09-10T01:00:00+00:00" (= 2026-09-10T01:00 UTC), but
    # is chronologically EARLIER. latest_scan must pick the +00:00 row.
    earlier_by_string = "2026-09-09T23:00:00-04:00"   # chronologically later (03:00 UTC)
    later_by_string_but_earlier = "2026-09-10T01:00:00+00:00"  # chronologically earlier (01:00 UTC)

    db.add(m.Scan(tenant_id=t.id, ts=later_by_string_but_earlier, raw={"n": 1}))
    db.add(m.Scan(tenant_id=t.id, ts=earlier_by_string, raw={"n": 2}))
    db.commit()

    latest = latest_scan(db, t.id)
    assert latest is not None
    assert latest.raw == {"n": 2}
    assert latest.ts == earlier_by_string


def test_latest_scan_none_when_no_scans(client):
    db = SessionLocal()
    t = m.Tenant(slug="empty", name="Empty Co")
    db.add(t)
    db.commit()
    assert latest_scan(db, t.id) is None


def test_parse_ts_reads_an_offset_without_a_colon_on_every_python():
    """The sensor's DNS filter writes `%z`, i.e. `-0400`, and
    `datetime.fromisoformat` accepts that only from Python 3.11. Production
    runs 3.13; this venv runs 3.10. Before `parse_ts` normalised it, this
    returned None here and a datetime there, so every DNS count read as zero
    in tests and correct in production."""
    import datetime as dt

    from guardian_hub.scans import parse_ts

    want = dt.datetime(2026, 9, 27, 21, 16, 41, tzinfo=dt.timezone.utc)
    assert parse_ts("2026-09-27T17:16:41-0400") == want
    assert parse_ts("2026-09-27T17:16:41-04:00") == want
    assert parse_ts("2026-09-28T02:46:41+0530") == want
    assert parse_ts("2026-09-27T17:16:41.500000-0400") == want + dt.timedelta(milliseconds=500)
    assert parse_ts("2026-09-27") == dt.datetime(2026, 9, 27, tzinfo=dt.timezone.utc)
    assert parse_ts("not a time") is None
