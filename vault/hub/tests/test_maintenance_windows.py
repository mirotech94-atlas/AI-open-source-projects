"""A planned window stops manufacturing fake downtime — and cannot be
back-dated to excuse an accident.

Both halves matter. Without windows, every patch night puts a real-looking
outage in the availability figure (trust plan §2.5B). With windows that can
be stretched after the fact, the availability figure becomes something an
operator can edit, which is worse than one that is merely wrong.
"""
import datetime as dt

import pytest

from guardian_hub import maintenance


class FakeTenant:
    def __init__(self, settings):
        self.settings = settings


def at(hour, minute=0):
    return dt.datetime(2026, 9, 20, hour, minute, tzinfo=dt.timezone.utc)


def test_a_recorded_window_is_readable_as_an_interval():
    s = maintenance.record({}, at(2), at(4))
    assert maintenance.windows(FakeTenant(s)) == [(at(2), at(4))]


def test_a_sample_inside_the_window_is_covered_and_one_outside_is_not():
    wins = maintenance.windows(FakeTenant(maintenance.record({}, at(2), at(4))))
    assert maintenance.covers(wins, at(3))
    assert not maintenance.covers(wins, at(1, 59))


def test_the_instant_a_window_ends_belongs_to_the_world_again():
    """Half-open [start, end). Closed at both ends would excuse one extra
    sample per window, which is small, wrong, and impossible to explain."""
    wins = maintenance.windows(FakeTenant(maintenance.record({}, at(2), at(4))))
    assert maintenance.covers(wins, at(2))
    assert not maintenance.covers(wins, at(4))


def test_cancelling_pulls_the_end_back_to_now():
    """The regression this file exists for.

    Open eight hours, cancel after ten minutes, then have a real outage two
    hours later. If cancelling left the original end in place, that outage
    would be silently excused by a window nobody was working in.
    """
    s = maintenance.record({}, at(2), at(10))
    s = maintenance.truncate_open(s, at(2, 10))
    wins = maintenance.windows(FakeTenant(s))
    assert wins == [(at(2), at(2, 10))]
    assert not maintenance.covers(wins, at(4)), "a cancelled window must not excuse a later outage"


def test_cancelling_does_not_reopen_or_extend_a_window_that_already_ended():
    s = maintenance.record({}, at(2), at(4))
    s = maintenance.truncate_open(s, at(9))
    assert maintenance.windows(FakeTenant(s)) == [(at(2), at(4))]


def test_cancelling_twice_changes_nothing_the_second_time():
    s = maintenance.truncate_open(maintenance.record({}, at(2), at(10)), at(3))
    once = maintenance.windows(FakeTenant(s))
    assert maintenance.windows(FakeTenant(maintenance.truncate_open(s, at(5)))) == once


def test_an_unreadable_window_excuses_nothing_rather_than_everything():
    """A bad row must not become an open-ended amnesty, and must not take
    the Assets page down either -- this runs behind every uptime figure."""
    bad = {"maintenance_windows": [
        {"starts": "not a date", "ends": "also not"},
        {"starts": None, "ends": None},
        "not even a dict",
        {"starts": at(6).isoformat()},                        # no end
        {"starts": at(8).isoformat(), "ends": at(7).isoformat()},  # inverted
    ]}
    assert maintenance.windows(FakeTenant(bad)) == []


def test_a_tenant_with_no_settings_has_no_windows():
    assert maintenance.windows(FakeTenant(None)) == []
    assert maintenance.windows(FakeTenant({})) == []


def test_windows_older_than_the_metrics_they_could_excuse_are_dropped():
    old = at(2) - dt.timedelta(days=maintenance.KEEP_DAYS + 1)
    s = {"maintenance_windows": [{"starts": old.isoformat(),
                                  "ends": (old + dt.timedelta(hours=1)).isoformat()}]}
    s = maintenance.record(s, at(2), at(4))
    assert maintenance.windows(FakeTenant(s)) == [(at(2), at(4))]


def test_recording_never_mutates_the_settings_it_was_given():
    """SQLAlchemy does not see an in-place edit of a JSON column, so a
    mutating helper would silently fail to persist."""
    original = {"other": "kept"}
    s = maintenance.record(original, at(2), at(4))
    assert original == {"other": "kept"}
    assert s["other"] == "kept"


# --------------------------------------------------------------------------
# End to end: the window has to actually move the number on the page.
# --------------------------------------------------------------------------

def _site_with_samples(tenant_id, name, when_up, when_down):
    """One site plus `up` metrics at the given instants."""
    from guardian_hub import models as m
    from guardian_hub.db import SessionLocal
    db = SessionLocal()
    try:
        db.add(m.Site(tenant_id=tenant_id, name=name, url=f"https://{name}.example",
                      last={"ok": True, "code": 200}))
        for when in when_up:
            db.add(m.Metric(tenant_id=tenant_id, ts=when.isoformat(), name="up",
                            entity=name, value=1.0))
        for when in when_down:
            db.add(m.Metric(tenant_id=tenant_id, ts=when.isoformat(), name="up",
                            entity=name, value=0.0))
        db.commit()
    finally:
        db.close()


def _card(client, name):
    return next(c for c in client.get("/api/v1/sites").json()["sites"] if c["name"] == name)


def _set_window(tenant_id, starts, ends):
    from guardian_hub import models as m
    from guardian_hub.db import SessionLocal
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tenant_id)
        tenant.settings = maintenance.record(dict(tenant.settings or {}), starts, ends)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def patch_night(client):
    """A site that was taken down deliberately for an hour last night."""
    from tests.test_read_write import seeded
    tid, _ = seeded(client)
    now = dt.datetime.now(dt.timezone.utc)
    # 40 good samples spread over the last 10 hours, then 20 failures packed
    # into a single hour three hours ago -- the reboot.
    up = [now - dt.timedelta(minutes=15 * i) for i in range(8, 48)]
    outage_start = now - dt.timedelta(hours=3)
    down = [outage_start + dt.timedelta(minutes=2 * i) for i in range(20)]
    _site_with_samples(tid, "shop", up, down)
    return tid, outage_start


def test_a_planned_reboot_stops_counting_as_an_outage(client, patch_night):
    tid, outage_start = patch_night
    before = _card(client, "shop")
    assert before["uptime_24h"] < 100, "the deliberate downtime should count until it is declared"
    assert before["uptime_24h_planned"] == 0

    _set_window(tid, outage_start - dt.timedelta(minutes=1),
                outage_start + dt.timedelta(hours=1))
    after = _card(client, "shop")

    assert after["uptime_24h"] == 100.0, "a declared window must not read as an outage"
    # Conservation, asserted rather than a hand-counted constant: every check
    # that left the denominator has to be accounted for as planned. If those
    # two ever disagree, checks are vanishing from the figure with nothing
    # saying where they went -- which is the exact failure mode publishing
    # the denominator was meant to make impossible.
    assert after["uptime_24h_planned"] == before["uptime_24h_checks"] - after["uptime_24h_checks"]
    assert after["uptime_24h_checks"] > 0, "the window must not swallow the whole window of data"


def test_an_unplanned_outage_outside_the_window_still_counts(client, patch_night):
    """The window excuses what it covers and nothing else -- otherwise
    declaring maintenance once would launder every later failure.

    The window here sits over healthy samples only, so it removes good
    checks while leaving all the failures in place. Availability therefore
    has to get WORSE, not better: a window is not a discount.
    """
    tid, outage_start = patch_night
    before = _card(client, "shop")
    _set_window(tid, outage_start - dt.timedelta(hours=5),
                outage_start - dt.timedelta(hours=4))
    after = _card(client, "shop")

    assert after["uptime_24h"] < 100, "the real outage is still an outage"
    assert after["uptime_24h"] < before["uptime_24h"]
    assert after["uptime_24h_planned"] == before["uptime_24h_checks"] - after["uptime_24h_checks"]
