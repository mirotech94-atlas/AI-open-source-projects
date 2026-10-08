"""A row the sensor stopped reporting must stop looking current.

The bug this closes: the hub upserts whatever the sensor sends and never
reconciles what it stopped sending, so a site dropped from the sensor's
config keeps its last result forever and presents it as a live check.

The most important tests here are the ones that assert NOTHING is flagged:
a healthy asset returns identical evidence on every sweep, and an earlier
version of this idea -- "flag rows whose evidence has not changed" -- would
have fired on every stable asset a tenant owns.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import assets, staleness

def _tenant(db, slug="stale-t"):
    t = m.Tenant(slug=slug, name="Stale T")
    db.add(t)
    db.commit()
    return t

def _scans(db, tenant_id, count, start="2026-09-12T10:00:00+00:00"):
    """`count` sweeps, six minutes apart, oldest first. Returns their ts."""
    base = dt.datetime.fromisoformat(start)
    out = []
    for i in range(count):
        ts = (base + dt.timedelta(minutes=6 * i)).isoformat()
        db.add(m.Scan(tenant_id=tenant_id, ts=ts, raw={}))
        out.append(ts)
    db.commit()
    return out

# --------------------------------------------------------------------------
# The cutoff
# --------------------------------------------------------------------------

def test_no_cutoff_until_there_are_enough_sweeps_to_judge(client):
    db = SessionLocal()
    t = _tenant(db)
    for n in range(staleness.STALE_AFTER_SWEEPS):
        assert staleness.stale_cutoff(db, t.id) is None
        _scans(db, t.id, 1, start=f"2026-09-12T1{n}:00:00+00:00")
    # Only once the window is full is there evidence anything was skipped.
    assert staleness.stale_cutoff(db, t.id) is not None
    db.close()

def test_cutoff_is_the_oldest_sweep_still_inside_the_window(client):
    db = SessionLocal()
    t = _tenant(db)
    ts = _scans(db, t.id, 10)
    cutoff = staleness.stale_cutoff(db, t.id)
    assert cutoff == dt.datetime.fromisoformat(ts[-staleness.STALE_AFTER_SWEEPS])
    db.close()

# --------------------------------------------------------------------------
# What is NOT stale -- the false positives this must never produce
# --------------------------------------------------------------------------

def test_a_row_reported_on_the_latest_sweep_is_not_stale(client):
    db = SessionLocal()
    t = _tenant(db)
    ts = _scans(db, t.id, 10)
    assert staleness.is_stale(ts[-1], staleness.stale_cutoff(db, t.id)) is False
    db.close()

def test_unchanged_evidence_is_not_staleness(client):
    # The whole design rests on this. A healthy site returns a byte-identical
    # result every sweep; that is success. Freshness is the timestamp, never
    # the content -- so a row whose `last` never changes but whose `last_seen`
    # keeps advancing is current, and is not flagged.
    db = SessionLocal()
    t = _tenant(db)
    ts = _scans(db, t.id, 10)
    same_evidence = {"ok": True, "code": 200}
    site = m.Site(tenant_id=t.id, name="steady", url="https://steady.example",
                  last=same_evidence, last_seen=ts[-1])
    db.add(site)
    db.commit()

    payload = assets.sites(db, t)
    row = next(s for s in payload.sites if s.name == "steady")
    assert row.stale is False
    assert row.stale_note == ""
    db.close()

def test_one_missed_sweep_is_a_transient_not_a_pattern(client):
    db = SessionLocal()
    t = _tenant(db)
    ts = _scans(db, t.id, 10)
    cutoff = staleness.stale_cutoff(db, t.id)
    # Missing from the newest sweep only.
    assert staleness.is_stale(ts[-2], cutoff) is False
    # ...and from the newest two. Still inside the window.
    assert staleness.is_stale(ts[-3], cutoff) is False
    db.close()

def test_a_row_with_no_last_seen_yet_is_not_stale(client):
    # Rows written before migration 0016 carry NULL. Calling those stale would
    # flag a tenant's entire estate the moment this shipped.
    db = SessionLocal()
    t = _tenant(db)
    _scans(db, t.id, 10)
    assert staleness.is_stale(None, staleness.stale_cutoff(db, t.id)) is False
    assert staleness.is_stale("", staleness.stale_cutoff(db, t.id)) is False
    db.close()

# --------------------------------------------------------------------------
# What IS stale
# --------------------------------------------------------------------------

def test_a_row_absent_from_the_whole_window_is_stale(client):
    db = SessionLocal()
    t = _tenant(db)
    ts = _scans(db, t.id, 10)
    cutoff = staleness.stale_cutoff(db, t.id)
    assert staleness.is_stale(ts[-(staleness.STALE_AFTER_SWEEPS + 1)], cutoff) is True
    assert staleness.is_stale(ts[0], cutoff) is True
    db.close()

def test_a_dropped_site_says_when_it_was_really_last_checked(client):
    db = SessionLocal()
    t = _tenant(db)
    ts = _scans(db, t.id, 10)
    db.add(m.Site(tenant_id=t.id, name="dropped", url="https://dropped.example",
                  last={"ok": True, "code": 200}, last_seen=ts[0]))
    db.commit()

    row = next(s for s in assets.sites(db, t).sites if s.name == "dropped")
    assert row.stale is True
    assert "stopped reporting" in row.stale_note
    assert "12 Sep 10:00 UTC" in row.stale_note
    assert row.last_checked == ts[0]
    db.close()

def test_a_dropped_host_is_flagged_the_same_way(client):
    db = SessionLocal()
    t = _tenant(db)
    ts = _scans(db, t.id, 10)
    db.add(m.Host(tenant_id=t.id, name="ghost-box", address="10.0.0.9",
                  last={"reachable": True}, last_seen=ts[0]))
    db.commit()

    row = next(h for h in assets.hosts(db, t).hosts if h.name == "ghost-box")
    assert row.stale is True
    assert "stopped reporting" in row.stale_note
    db.close()

def test_staleness_raises_no_alert_and_changes_no_severity(client):
    # A frozen row is the absence of a finding, not a new one. Nothing may
    # arrive in the inbox because of it.
    db = SessionLocal()
    t = _tenant(db)
    ts = _scans(db, t.id, 10)
    db.add(m.Site(tenant_id=t.id, name="dropped", url="https://dropped.example",
                  last={"ok": True}, last_seen=ts[0]))
    db.commit()
    before = db.query(m.Alert).count()

    assets.sites(db, t)

    assert db.query(m.Alert).count() == before
    db.close()

# --------------------------------------------------------------------------
# Ingest keeps the column moving
# --------------------------------------------------------------------------

def test_ingest_stamps_last_seen_on_every_sweep(client):
    from guardian_hub.ingest import store_scan

    db = SessionLocal()
    t = _tenant(db, slug="ingest-stale")
    payload = {"last": {"ts": "2026-09-12T12:00:00+00:00",
                        "sites": [{"name": "s1", "url": "https://s1.example", "ok": True}],
                        "ssh": [{"name": "h1", "target": "root@10.0.0.1", "reachable": True}]}}
    store_scan(db, t, None, payload, deliver=False)
    db.commit()
    assert db.query(m.Site).filter_by(tenant_id=t.id, name="s1").one().last_seen == "2026-09-12T12:00:00+00:00"
    assert db.query(m.Host).filter_by(tenant_id=t.id, name="h1").one().last_seen == "2026-09-12T12:00:00+00:00"

    payload["last"]["ts"] = "2026-09-12T12:06:00+00:00"
    store_scan(db, t, None, payload, deliver=False)
    db.commit()
    assert db.query(m.Site).filter_by(tenant_id=t.id, name="s1").one().last_seen == "2026-09-12T12:06:00+00:00"
    db.close()

def test_a_site_the_sensor_drops_keeps_its_row_and_its_history(client):
    # Deliberately NOT pruned like recommendations are: a site the owner once
    # watched is still a site they watched, and deleting the row would take
    # its metrics and incident history with it. It is marked, not removed.
    from guardian_hub.ingest import store_scan

    db = SessionLocal()
    t = _tenant(db, slug="drop-keeps")
    payload = {"last": {"ts": "2026-09-12T12:00:00+00:00",
                        "sites": [{"name": "kept", "url": "https://kept.example", "ok": True}]}}
    store_scan(db, t, None, payload, deliver=False)
    db.commit()

    # Next sweeps report no sites at all.
    for i in range(1, 6):
        payload["last"]["ts"] = f"2026-09-12T12:{6 * i:02d}:00+00:00"
        payload["last"]["sites"] = []
        store_scan(db, t, None, payload, deliver=False)
    db.commit()

    row = db.query(m.Site).filter_by(tenant_id=t.id, name="kept").one()
    assert row.last_seen == "2026-09-12T12:00:00+00:00"
    assert next(s for s in assets.sites(db, t).sites if s.name == "kept").stale is True
    db.close()


# --------------------------------------------------------------------------
# The Security page -- the surface the original complaint came from
# --------------------------------------------------------------------------
# Every check `security.checks` emits is stamped `last_verified` with the
# CURRENT scan's timestamp. On a row the sensor stopped reporting that is not
# just out of date, it is a false claim: the check says it was verified on the
# latest sweep when nothing looked at the asset at all.

def test_a_dropped_site_stops_asserting_a_security_verdict(client):
    from guardian_hub.payloads import security

    db = SessionLocal()
    t = _tenant(db, slug="sec-stale")
    ts = _scans(db, t.id, 10)
    db.add(m.Site(tenant_id=t.id, name="dropped", url="https://dropped.example",
                  last={"ok": True, "sec": {"csp": False, "hsts": True}}, last_seen=ts[0]))
    db.add(m.Site(tenant_id=t.id, name="current", url="https://current.example",
                  last={"ok": True, "sec": {"csp": False, "hsts": True}}, last_seen=ts[-1]))
    db.commit()

    rows = security.checks(db, t, {}, ts[-1])
    targets = {c.target for c in rows}
    assert "current" in targets
    assert "dropped" not in targets, "a check was claimed on a site nobody is checking"
    db.close()


def test_a_dropped_host_gets_no_scorecard_grade(client):
    # A hardening grade for an unchecked host is the same false claim in a
    # different shape -- a letter grade reads as a current assessment.
    from guardian_hub.payloads import security

    db = SessionLocal()
    t = _tenant(db, slug="sec-stale-host")
    ts = _scans(db, t.id, 10)
    db.add(m.Host(tenant_id=t.id, name="ghost", address="10.0.0.9",
                  last={"reachable": True}, last_seen=ts[0]))
    db.add(m.Host(tenant_id=t.id, name="live-box", address="10.0.0.8",
                  last={"reachable": True}, last_seen=ts[-1]))
    db.commit()

    targets = {sc.target for sc in security.scorecards(db, t, {})}
    assert "live-box" in targets
    assert "ghost" not in targets
    db.close()


def test_security_still_reports_everything_that_is_being_checked(client):
    # The guard against over-correcting: excluding stale rows must not quietly
    # drop live ones, which would hide real failures.
    from guardian_hub.payloads import security

    db = SessionLocal()
    t = _tenant(db, slug="sec-none-stale")
    ts = _scans(db, t.id, 10)
    for i in range(3):
        db.add(m.Site(tenant_id=t.id, name=f"s{i}", url=f"https://s{i}.example",
                      last={"ok": True, "sec": {"csp": False}}, last_seen=ts[-1]))
    db.commit()

    targets = {c.target for c in security.checks(db, t, {}, ts[-1])}
    for i in range(3):
        assert f"s{i}" in targets
    db.close()


def test_rows_with_no_last_seen_are_still_reported_on_security(client):
    # Pre-0016 rows must not vanish from the Security page the moment this
    # ships -- NULL means "not yet known", not "not being checked".
    from guardian_hub.payloads import security

    db = SessionLocal()
    t = _tenant(db, slug="sec-null")
    ts = _scans(db, t.id, 10)
    db.add(m.Site(tenant_id=t.id, name="legacy", url="https://legacy.example",
                  last={"ok": True, "sec": {"csp": False}}, last_seen=None))
    db.commit()

    assert "legacy" in {c.target for c in security.checks(db, t, {}, ts[-1])}
    db.close()
