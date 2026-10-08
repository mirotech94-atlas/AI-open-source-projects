"""Item 05: nothing may fall out of the posture picture silently.

A target the sensor has stopped reporting is excluded from SCORING on
purpose — a card stamped "verified now" for an asset nothing looked at is
worse than no card. Excluding it without saying so is the separate bug: the
client counts the cards and believes that is the estate.
"""
import datetime as dt

from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.payloads import security as security_payload
from tests.test_auth import seed_user


def _iso(days_ago: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")


def _sweeps(db, tid, n=4):
    """Staleness is judged against the Nth most recent sweep, not the clock:
    with fewer sweeps on record than the window is wide there is no evidence
    anything was skipped (`staleness.stale_cutoff` returns None, "judge
    nothing"). So a test about staleness has to have a sweep history."""
    for i in range(n):
        db.add(m.Scan(tenant_id=tid, ts=_iso(i), score=90, health={}, raw={}))


def _coverage(tid):
    db = SessionLocal()
    try:
        tenant = db.query(m.Tenant).filter_by(id=tid).one()
        return security_payload.posture_coverage(db, tenant)
    finally:
        db.close()


def test_a_fresh_estate_is_fully_covered(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    _sweeps(db, tid)
    db.add(m.Host(tenant_id=tid, name="vps1", address="1.2.3.4", last={}, last_seen=_iso(0)))
    db.add(m.Site(tenant_id=tid, name="roots", url="https://example.invalid", last={}, last_seen=_iso(0)))
    db.commit(); db.close()
    cov = _coverage(tid)
    # +1 for the home-network card, built from the current scan.
    assert (cov.scored, cov.total, cov.unscored) == (3, 3, [])


def test_a_target_gone_quiet_is_counted_and_named_even_though_it_is_not_scored(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    _sweeps(db, tid)
    db.add(m.Host(tenant_id=tid, name="vps1", address="1.2.3.4", last={}, last_seen=_iso(0)))
    db.add(m.Host(tenant_id=tid, name="forgotten", address="5.6.7.8", last={}, last_seen=_iso(60)))
    db.commit(); db.close()
    cov = _coverage(tid)
    assert cov.total == 3 and cov.scored == 2
    assert cov.unscored == ["forgotten (server)"], "a machine that has gone quiet must not vanish from the count"


def test_with_too_little_history_nothing_is_judged_stale(client):
    """A tenant that just enrolled has no evidence anything was skipped, so
    every target counts as covered rather than being accused."""
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(m.Host(tenant_id=tid, name="ancient", address="5.6.7.8", last={}, last_seen=_iso(400)))
    db.commit(); db.close()
    cov = _coverage(tid)
    assert cov.unscored == [] and cov.scored == cov.total


def test_a_server_and_a_website_of_the_same_name_are_told_apart(client):
    """"Roots" is both a server and a website on the live hub. An
    unqualified list read "Roots, Roots" and sent the owner looking at the
    wrong one."""
    _uid, tid = seed_user()
    db = SessionLocal()
    _sweeps(db, tid)
    db.add(m.Host(tenant_id=tid, name="Roots", address="root@1.2.3.4", last={}, last_seen=_iso(60)))
    db.add(m.Site(tenant_id=tid, name="Roots", url="https://example.invalid", last={}, last_seen=_iso(60)))
    db.commit(); db.close()
    cov = _coverage(tid)
    assert cov.unscored == ["Roots (server)", "Roots (website)"]
    assert len(set(cov.unscored)) == 2, "two different things must not read as one name twice"
