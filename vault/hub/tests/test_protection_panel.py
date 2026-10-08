"""The protection panel's numbers come from rows, not from a feeling.

Every count here is a group-by over `dns_query`, so the figure on the page
can be checked against the lookups underneath it. The posture half comes
from the sensor's own host, because that is the only machine Vault can
see inside.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import security as sec
from tests.test_auth import seed_user


def _q(tid, name, blocked, lst, when=None, mac=None):
    ts = (when or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
    return m.DnsQuery(tenant_id=tid, ts=ts, name=name, blocked=blocked, list=lst, mac=mac,
                      client="192.168.1.50", qtype="A")


def _panel(tid, raw=None):
    db = SessionLocal()
    try:
        return sec.protection(db, db.get(m.Tenant, tid), raw or {})
    finally:
        db.close()


def test_blocks_are_counted_by_what_they_are_not_by_list_name(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_q(tid, "a.onion", True, "dark-web"), _q(tid, "b.onion", True, "dark-web"),
                _q(tid, "bad.example", True, "urlhaus"),
                _q(tid, "ok.example", False, None)])       # not blocked: must not count
    db.commit(); db.close()

    p = _panel(tid)
    tally = {b.category: b.count for b in p.blocked}
    assert tally == {"Dark web": 2, "Malware": 1}
    assert p.blocked_total == 3
    assert p.filtering is True


def test_an_empty_week_is_not_the_same_as_a_filter_that_is_not_running(client):
    """Zero blocks with the filter answering is good news. Zero rows at all
    means nothing is being filtered, which is a setup problem. The panel has
    to be able to tell them apart."""
    _uid, tid = seed_user()
    assert _panel(tid).filtering is False            # no rows at all

    db = SessionLocal(); db.add(_q(tid, "fine.example", False, None)); db.commit(); db.close()
    p = _panel(tid)
    assert p.filtering is True and p.blocked_total == 0


def test_old_blocks_fall_out_of_the_window(client):
    _uid, tid = seed_user()
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    db = SessionLocal()
    db.add_all([_q(tid, "old.onion", True, "dark-web", when=old),
                _q(tid, "new.onion", True, "dark-web")])
    db.commit(); db.close()
    assert _panel(tid).blocked_total == 1


def test_recent_lookups_name_the_device_not_a_mac(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(m.Device(tenant_id=tid, mac="aa:bb:cc:dd:ee:ff", name="raw", label="Staffphone"))
    db.add(_q(tid, "abcd.onion", True, "dark-web", mac="aa:bb:cc:dd:ee:ff"))
    db.commit(); db.close()
    r = _panel(tid).recent
    assert len(r) == 1 and r[0].device == "Staffphone" and r[0].category == "Dark web"


def test_a_check_that_could_not_run_is_unknown_not_off(client):
    """The rule the whole panel rests on: None means we could not tell, and
    reporting that as a failure teaches the owner to ignore the page."""
    _uid, tid = seed_user()
    raw = {"endpoint": {"platform": "mac", "checks":
                        {"sip": True, "filevault": False, "firewall": None}}}
    states = {d.key: d.state for d in _panel(tid, raw).defences}
    assert states == {"sip": "on", "filevault": "off", "firewall": "unknown"}


def test_what_is_off_is_listed_first(client):
    """A page you scan top-down should put the thing that needs doing at the
    top of the list."""
    _uid, tid = seed_user()
    raw = {"endpoint": {"platform": "mac", "checks":
                        {"sip": True, "gatekeeper": True, "filevault": False}}}
    assert _panel(tid, raw).defences[0].key == "filevault"


def test_no_sensor_report_yields_no_defences_rather_than_a_clean_bill(client):
    _uid, tid = seed_user()
    p = _panel(tid, {})
    assert p.defences == [] and p.defences_host == ""
