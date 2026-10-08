"""Task 58 gap G1: the sensor-liveness watchdog.

The predecessor ran this as a systemd timer on the hub host
(`nexus-hub-watch`, pushing "hub silent" when the newest scan was older than
25 minutes). Vault had no equivalent, so a dead sensor showed only as a
stale pill. These tests pin the behaviour that replaces it.
"""

import datetime as dt

import pytest

from guardian_hub import models as m
from guardian_hub import scheduler
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo

NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.timezone.utc)


class Clock:
    """A movable "now" for the scheduler, so a test can let time pass without
    rewriting scan rows (the job reads the NEWEST scan, so you cannot make a
    sensor go quiet again by inserting an older row)."""

    def __init__(self, at: dt.datetime):
        self.at = at

    def advance(self, minutes: float) -> None:
        self.at += dt.timedelta(minutes=minutes)

    def __call__(self) -> dt.datetime:
        return self.at


@pytest.fixture()
def watched(client, monkeypatch):
    """A seeded tenant that is NOT a demo tenant (the job skips those, since
    demo history is a one-shot backfill that is stale by design), with every
    delivery captured instead of sent."""
    tenant_id = seeded_demo(client)
    clock = Clock(NOW)
    monkeypatch.setattr(scheduler, "_now", clock)

    sent: list[tuple[str, str, str]] = []

    from guardian_hub.integrations import registry

    def capture(tid, msg):
        sent.append((tid, msg.kind, msg.severity))

    monkeypatch.setattr(registry, "deliver_background", capture)

    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tenant_id)
        tenant.settings = {k: v for k, v in (tenant.settings or {}).items() if k != "demo_code"}
        db.commit()
    finally:
        db.close()
    return tenant_id, sent, clock


def _set_newest_scan(tenant_id: str, age_min: float, raw: dict | None = None) -> None:
    """Leave the tenant with exactly ONE scan, `age_min` old relative to NOW.

    The seeder's own 30 days of scans are cleared first: they are stamped
    relative to the real wall clock, so keeping them would make "is the newest
    scan stale" depend on what time of day the suite runs.

    `raw` is that scan's payload. It matters for the load diagnosis below:
    the sensor reports its own host's `self.load1` and `self.cores` on every
    scan, so the last scan the hub DID receive can say why the next one
    never came.
    """
    db = SessionLocal()
    try:
        db.query(m.Scan).filter_by(tenant_id=tenant_id).delete()
        ts = (NOW - dt.timedelta(minutes=age_min)).isoformat(timespec="seconds")
        db.add(m.Scan(tenant_id=tenant_id, ts=ts, raw=raw or {}, health={}))
        db.commit()
    finally:
        db.close()


def _alert(tenant_id: str):
    db = SessionLocal()
    try:
        return db.query(m.Alert).filter_by(tenant_id=tenant_id,
                                          key=scheduler.SENSOR_SILENT_KEY).first()
    finally:
        db.close()


def test_fresh_sensor_raises_nothing(watched):
    tenant_id, sent, _clock = watched
    _set_newest_scan(tenant_id, 3)
    scheduler.sensor_watch_job()
    assert _alert(tenant_id) is None
    assert sent == []


def test_silent_sensor_raises_a_crit_alert_and_delivers_once(watched):
    tenant_id, sent, _clock = watched
    _set_newest_scan(tenant_id, 40)
    scheduler.sensor_watch_job()

    row = _alert(tenant_id)
    assert row is not None
    assert row.sev == "CRIT" and row.status == "open"
    assert "40 min" in row.msg
    assert row.evidence["threshold_min"] == scheduler.DEFAULT_SILENT_MIN
    assert row.confidence == "high"
    # The network is attributed, so the Inbox groups it like any other alert
    # rather than showing an unscoped row.
    assert row.network_id
    assert sent == [(tenant_id, "alert", "CRIT")]


def test_still_silent_does_not_re_deliver(watched):
    tenant_id, sent, _clock = watched
    _set_newest_scan(tenant_id, 40)
    scheduler.sensor_watch_job()
    scheduler.sensor_watch_job()
    scheduler.sensor_watch_job()

    row = _alert(tenant_id)
    assert row.count == 1                      # repeating is the resend ladder's job
    assert len(sent) == 1


def test_recovery_resolves_the_alert_and_says_so(watched):
    tenant_id, sent, _clock = watched
    _set_newest_scan(tenant_id, 40)
    scheduler.sensor_watch_job()
    _set_newest_scan(tenant_id, 1)             # data came back
    scheduler.sensor_watch_job()

    row = _alert(tenant_id)
    assert row.status == "resolved" and row.resolved_by == "auto"
    assert sent == [(tenant_id, "alert", "CRIT"), (tenant_id, "resolve", "CRIT")]

    # A second recovery tick is silent -- already resolved.
    scheduler.sensor_watch_job()
    assert len(sent) == 2


def test_going_silent_again_after_a_recovery_re_fires(watched):
    tenant_id, sent, clock = watched
    _set_newest_scan(tenant_id, 40)
    scheduler.sensor_watch_job()
    _set_newest_scan(tenant_id, 1)
    scheduler.sensor_watch_job()
    clock.advance(120)                          # and then it goes quiet again
    scheduler.sensor_watch_job()

    row = _alert(tenant_id)
    assert row.status == "open" and row.count == 2
    assert [k for _, k, _ in sent] == ["alert", "resolve", "alert"]


def test_demo_tenant_is_skipped(client, monkeypatch):
    tenant_id = seeded_demo(client)             # keeps its demo_code
    monkeypatch.setattr(scheduler, "_now", Clock(NOW))
    _set_newest_scan(tenant_id, 500)
    scheduler.sensor_watch_job()
    assert _alert(tenant_id) is None


def test_tenant_with_no_scans_is_skipped(client, monkeypatch):
    monkeypatch.setattr(scheduler, "_now", Clock(NOW))
    db = SessionLocal()
    try:
        t = m.Tenant(name="Empty", slug="empty")
        db.add(t)
        db.commit()
        tid = t.id
    finally:
        db.close()
    scheduler.sensor_watch_job()
    assert _alert(tid) is None


def test_threshold_comes_from_config_and_is_floored(watched):
    tenant_id, _sent, _clock = watched
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tenant_id)
        tenant.config = {**(tenant.config or {}), "thresholds": {"sensor_silent_min": 90}}
        db.commit()
    finally:
        db.close()

    _set_newest_scan(tenant_id, 60)
    scheduler.sensor_watch_job()
    assert _alert(tenant_id) is None            # 60 min < the configured 90

    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tenant_id)
        tenant.config = {**(tenant.config or {}), "thresholds": {"sensor_silent_min": 1}}
        db.commit()
    finally:
        db.close()
    assert scheduler._silent_threshold_min(_tenant(tenant_id)) == scheduler.MIN_SILENT_MIN


def _tenant(tenant_id: str) -> m.Tenant:
    db = SessionLocal()
    try:
        return db.get(m.Tenant, tenant_id)
    finally:
        db.close()


def test_unparseable_timestamp_is_skipped_not_crashed(watched):
    tenant_id, sent, _clock = watched
    db = SessionLocal()
    try:
        db.query(m.Scan).filter_by(tenant_id=tenant_id).delete()
        db.add(m.Scan(tenant_id=tenant_id, ts="not-a-timestamp", raw={}, health={}))
        db.commit()
    finally:
        db.close()
    scheduler.sensor_watch_job()                # must not raise
    assert sent == []
    assert _alert(tenant_id) is None


def test_job_is_registered_every_five_minutes():
    ids = {j.id for j in scheduler.jobs()}
    assert "sensor-watch" in ids
    field = scheduler.SENSOR_WATCH_CRON.fields_map["minute"]
    assert "*/5" in str(field)


# --------------------------------------------------------------------------
# Why the sensor went quiet, when the last scan already said
# --------------------------------------------------------------------------
#
# The alert used to guess: "Either the sensor process stopped, its host is
# off, or it cannot reach the hub." On 2026-09-21 none of the three was true.
# The process was running, the host was on, and every push had logged "hub
# ok" -- the Mac was at load 105 on 10 cores, so a scan that normally takes
# 1.6 minutes was taking 16, and the newest scan the hub held was always
# older than the threshold. The real cause was not on the list.
#
# The sensor already reports `self.load1` and `self.cores` on every scan, and
# the hub already stores them. It just never read them.

def _self(load1, cores):
    return {"self": {"version": "0.1.0", "load1": load1, "cores": cores}}


def test_an_overloaded_host_is_named_instead_of_the_three_guesses(watched):
    tenant_id, sent, _clock = watched
    _set_newest_scan(tenant_id, 40, _self(105.46, 10))
    scheduler.sensor_watch_job()

    row = _alert(tenant_id)
    why = row.why.lower()
    assert "105" in row.why and "10 cores" in row.why
    assert "10.5" in row.why, "say how oversubscribed, not just the raw load"
    assert "load" in why
    # The three guesses are gone: none of them was true the day this was
    # written, and offering them beside the real answer buries it.
    assert "process stopped" not in why and "host is off" not in why

    assert row.evidence["load1"] == 105.46
    assert row.evidence["cores"] == 10
    assert row.evidence["load_per_core"] == 10.5

    # The headline and the severity do NOT soften. Vault genuinely cannot
    # see the network; only the explanation improves.
    assert row.sev == "CRIT"
    assert "40 min" in row.msg and "cannot see this network" in row.msg
    assert sent == [(tenant_id, "alert", "CRIT")]


def test_a_host_that_is_not_busy_keeps_the_original_wording(watched):
    """Below the threshold the load is not the story, and saying so anyway
    would point the reader at a healthy number."""
    tenant_id, _sent, _clock = watched
    _set_newest_scan(tenant_id, 40, _self(1.2, 10))
    scheduler.sensor_watch_job()

    row = _alert(tenant_id)
    assert "process stopped" in row.why and "cannot reach the hub" in row.why
    assert "load" not in row.why.lower()
    assert "load_per_core" not in row.evidence


def test_a_sensor_that_reports_no_load_still_raises_the_alert(watched):
    """`self.load1` is None on a platform with no load average, and an older
    sensor has no `self` block at all. Neither may cost the alert."""
    tenant_id, sent, _clock = watched
    for raw in ({}, {"self": {}}, _self(None, 10), _self(4.0, None), _self(4.0, 0)):
        db = SessionLocal()
        try:
            db.query(m.Alert).filter_by(tenant_id=tenant_id,
                                        key=scheduler.SENSOR_SILENT_KEY).delete()
            db.commit()
        finally:
            db.close()
        sent.clear()
        _set_newest_scan(tenant_id, 40, raw)
        scheduler.sensor_watch_job()

        row = _alert(tenant_id)
        assert row is not None and row.sev == "CRIT", raw
        assert "process stopped" in row.why, raw
        assert sent == [(tenant_id, "alert", "CRIT")], raw
