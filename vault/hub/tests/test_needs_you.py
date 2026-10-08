"""The Overview's lead: the two-button queue, and its counterweight.

The design's first three seconds are "here is what needs you". The danger is
putting the wrong thing behind an Approve button — a repair Vault has
already run, or an errand it cannot perform — because then the button is
lying about what it does.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import overview as ov
from tests.test_auth import seed_user


def _alert(tid, key, sev="WARN", msg="", status="open"):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return m.Alert(tenant_id=tid, key=key, sev=sev, msg=msg or key,
                   first_seen=now, last_seen=now, status=status)


def _fix(tid, by, ok=True, ago_days=1):
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=ago_days)).isoformat(timespec="seconds")
    return m.Fix(tenant_id=tid, ts=ts, host="vps1", action="enable_ufw", by=by, ok=ok)


def _needs(tid):
    db = SessionLocal()
    try:
        return ov.needs_you(db, db.get(m.Tenant, tid))
    finally:
        db.close()


def _fixed(tid, days=14):
    db = SessionLocal()
    try:
        return ov.fixed_without_you(db, tid, days)
    finally:
        db.close()


def test_a_settled_alert_never_asks_for_approval(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_alert(tid, "sec:ufw:vps1", status="acked"),
                _alert(tid, "sec:sshpw:vps1", status="resolved")])
    db.commit(); db.close()
    assert _needs(tid) == []


def test_only_approval_remedies_reach_the_queue(client):
    """The whole contract. Whatever is in this list, every row must be
    something a yes actually runs."""
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_alert(tid, "sec:ufw:vps1", "WARN", "firewall is off"),
                _alert(tid, "home:rogue-dhcp", "CRIT", "rogue dhcp"),   # human errand
                _alert(tid, "email:dmarc-none:x.com", "INFO", "dmarc")])
    db.commit(); db.close()

    from guardian_hub import remedy as remedy_mod
    db = SessionLocal()
    t = db.get(m.Tenant, tid)
    for item in ov.needs_you(db, t):
        alert = db.query(m.Alert).filter_by(tenant_id=tid, key=item.key).one()
        assert remedy_mod.resolve(db, t, alert, "admin").kind == "approval"
    db.close()


def test_a_human_errand_is_never_behind_an_approve_button(client):
    """`home:rogue-dhcp` needs somebody to unplug a router. Approve cannot do
    that, so the row must not appear."""
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(_alert(tid, "home:rogue-dhcp", "CRIT", "rogue dhcp"))
    db.commit(); db.close()
    assert [i.key for i in _needs(tid)] == []


def test_the_queue_is_capped_and_leads_with_the_worst(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_alert(tid, f"sec:ufw:h{i}", "WARN", f"firewall off h{i}") for i in range(10)]
               + [_alert(tid, "sec:ufw:crit", "CRIT", "firewall off, critical host")])
    db.commit(); db.close()
    got = _needs(tid)
    assert len(got) <= ov.NEEDS_YOU_MAX
    if got:
        assert got[0].severity == "CRIT", "a CRIT decision must not sit below WARNs"


def test_every_row_says_what_it_would_do(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(_alert(tid, "sec:ufw:vps1", "WARN", "firewall is off"))
    db.commit(); db.close()
    for item in _needs(tid):
        assert item.title, "a row with no title is an unlabelled button"
        assert item.detail or item.cost, "approving must never be a blind yes"


def test_fixed_splits_guardians_work_from_yours(client):
    """`account.is_person` decides, so the stamps here are the ones the code
    actually writes: the sensor stamps `auto` and `accepted-recommendation`,
    the hub stamps `hub:<user>` (`ingest.store_scan`, `runner.poll_queue`,
    `fix.apply_fix`). A bare username is not one of them, and treating any
    non-"auto" stamp as an approval is the bug this split had: 61 rows
    stamped "test" on the live hub were reported as the owner's own yeses."""
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_fix(tid, "auto"), _fix(tid, "auto"), _fix(tid, "hub:miro")])
    db.commit(); db.close()
    got = _fixed(tid)
    assert (got.automatic, got.approved) == (2, 1)


def test_a_stamp_nobody_recognises_is_guardians_own_work(client):
    """The guard for the regression above: an unknown stamp must never be
    counted as a person's approval, in either the totals or the day buckets."""
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_fix(tid, "test"), _fix(tid, "sensor-7"), _fix(tid, "accepted-recommendation")])
    db.commit(); db.close()
    got = _fixed(tid)
    assert (got.automatic, got.approved) == (2, 1)
    assert (sum(d.automatic for d in got.daily), sum(d.approved for d in got.daily)) == (2, 1)


def test_a_failed_repair_is_not_counted_as_done(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_fix(tid, "auto"), _fix(tid, "auto", ok=False)])
    db.commit(); db.close()
    assert _fixed(tid).automatic == 1


def test_older_repairs_fall_out_of_the_window(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_fix(tid, "auto", ago_days=2), _fix(tid, "auto", ago_days=90)])
    db.commit(); db.close()
    got = _fixed(tid, days=14)
    assert got.automatic == 1 and got.days == 14
