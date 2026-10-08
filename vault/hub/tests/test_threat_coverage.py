"""The attack-type grid must never claim coverage the sensor does not have.

Vault is asked, in plain words, what it does about phishing, malware,
man-in-the-middle, DoS and ransomware. The honest answer differs per attack,
and two of those answers are "it does not stop this". These tests pin the
honest answers in place so a later edit cannot quietly upgrade them.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import security as sec
from tests.test_auth import seed_user


def _alert(tid, key, sev="WARN", msg="", status="open"):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return m.Alert(tenant_id=tid, key=key, sev=sev, msg=msg or key,
                   first_seen=now, last_seen=now, status=status)


def _classes(tid):
    db = SessionLocal()
    try:
        return {t.key: t for t in sec.threats(db, db.get(m.Tenant, tid))}
    finally:
        db.close()


def test_every_attack_the_owner_named_has_a_row(client):
    _uid, tid = seed_user()
    got = _classes(tid)
    for want in ("phishing", "malware", "mitm", "dos", "ransomware", "darkweb", "breakin"):
        assert want in got, f"no row for {want}"
        assert got[want].what and got[want].does, f"{want} has an empty explanation"


def test_dos_admits_it_only_sees_the_damage(client):
    """The most tempting row to overstate. Vault measures a site going
    down; it cannot absorb a flood, and the card has to say so."""
    _uid, tid = seed_user()
    t = _classes(tid)["dos"]
    assert t.stance == "effect-only"
    assert t.limit, "the DoS row must state what Guardian cannot do"
    assert "cannot" in t.limit.lower()


def test_ransomware_does_not_claim_detection(client):
    _uid, tid = seed_user()
    t = _classes(tid)["ransomware"]
    assert t.stance == "indirect"
    assert "backup" in t.limit.lower(), "the one thing that actually saves you must be named"


def test_malware_row_refuses_to_call_itself_antivirus(client):
    _uid, tid = seed_user()
    t = _classes(tid)["malware"]
    assert "not an antivirus" in t.limit.lower()
    assert "phones" in t.limit.lower(), "the unscanned devices must be named"


def test_open_alerts_land_in_the_class_they_belong_to(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([
        _alert(tid, "home:route", "CRIT", "Default route is not the router"),
        _alert(tid, "lan:guest:second-router:aa:bb", "WARN", "Second router"),
        _alert(tid, "bruteforce:vps1", "WARN", "900 failed SSH logins"),
        _alert(tid, "site:shop:down", "CRIT", "shop DOWN"),
        _alert(tid, "sec:miner:vps1", "CRIT", "crypto-miner running"),
    ])
    db.commit(); db.close()

    got = _classes(tid)
    assert got["mitm"].open_count == 2
    assert got["breakin"].open_count == 1
    assert got["dos"].open_count == 1
    assert got["malware"].open_count == 1
    assert got["ransomware"].open_count == 0


def test_crit_signals_are_shown_before_warn(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_alert(tid, "wan:loss:cloudflare", "WARN", "packet loss"),
                _alert(tid, "wan:path", "CRIT", "line down")])
    db.commit(); db.close()
    assert _classes(tid)["dos"].signals[0].severity == "CRIT"


def test_a_settled_alert_is_not_an_open_signal(client):
    """An acked or resolved alert is gone from the Inbox, so it must be gone
    from here too -- the two views cannot disagree about what is open."""
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_alert(tid, "home:evil-twin", "CRIT", "evil twin", status="acked"),
                _alert(tid, "home:rogue-dhcp", "CRIT", "rogue dhcp", status="resolved")])
    db.commit(); db.close()
    assert _classes(tid)["mitm"].open_count == 0


def test_blocked_lookups_are_credited_to_the_right_attack(client):
    _uid, tid = seed_user()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    def q(name, lst):
        return m.DnsQuery(tenant_id=tid, ts=now, name=name, blocked=True, list=lst,
                          client="192.168.1.50", qtype="A")

    db = SessionLocal()
    db.add_all([q("a.onion", "dark-web"), q("b.onion", "dark-web"),
                q("pay-now.example", "phishing-army"), q("c2.example", "urlhaus")])
    db.commit(); db.close()

    got = _classes(tid)
    assert got["darkweb"].blocked == 2
    assert got["phishing"].blocked == 1
    assert got["malware"].blocked == 1
    assert got["mitm"].blocked == 0, "no blocklist stops a man in the middle"


def test_the_panel_carries_the_classes(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    try:
        p = sec.protection(db, db.get(m.Tenant, tid), {})
    finally:
        db.close()
    assert [t.key for t in p.threats] == [s["key"] for s in sec.THREAT_SPEC]
