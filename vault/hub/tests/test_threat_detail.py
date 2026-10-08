"""One attack class, opened up: what Vault found and how it was settled.

The card in `ThreatMatrix` answers "does Vault do anything about this, and
is anything wrong right now". Clicking it asks a different question -- what did
it actually find, and for the things that are no longer open, how were they
dealt with.

The card cannot answer the second half, and not because of the UI. `threats()`
builds `signals` from OPEN alerts only (`m.SETTLED_ALERT_STATUSES` excluded),
capped at four. A settled alert is by definition not open, so a drawer fed from
that data could only ever show the problems Vault has NOT dealt with -- the
exact inverse of "how did you solve it".

So the detail reads settled alerts too, and carries what settled them.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import security as sec
from tests.test_auth import seed_user


def _alert(db, tid, key, sev="WARN", msg="something", status="open",
           fix="", resolved_by="", why="", evidence=None):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    db.add(m.Alert(tenant_id=tid, key=key, sev=sev, msg=msg, status=status,
                   fix=fix, resolved_by=resolved_by, why=why,
                   evidence=evidence or {}, first_seen=now, last_seen=now, count=1))


def test_the_detail_shows_settled_alerts_the_card_cannot(client):
    """The whole reason this endpoint exists. `threats()` filters settled
    alerts out, so "how was it solved" is unanswerable from the card's data."""
    _uid, tid = seed_user()
    db = SessionLocal()
    _alert(db, tid, "email:spf:missing", sev="WARN", msg="SPF missing", status="open")
    _alert(db, tid, "email:dmarc:none", sev="CRIT", msg="DMARC not enforced",
           status="resolved", fix="dns_record_add", resolved_by="guardian")
    db.commit()

    tenant = db.get(m.Tenant, tid)
    card = [t for t in sec.threats(db, tenant) if t.key == "phishing"][0]
    detail = sec.threat_detail(db, tenant, "phishing")
    db.close()

    assert card.open_count == 1                      # the card sees only the open one
    assert len(detail.findings) == 2                 # the detail sees both
    settled = [f for f in detail.findings if f.resolved]
    assert len(settled) == 1
    assert settled[0].fix == "dns_record_add"
    assert settled[0].resolved_by == "guardian"


def test_settled_means_the_same_thing_here_as_everywhere_else(client):
    """`m.SETTLED_ALERT_STATUSES` is resolved|snoozed|acked, and it exists
    because five hand-copied literals had already drifted apart with `acked`
    missing from some. This must not become a sixth."""
    _uid, tid = seed_user()
    db = SessionLocal()
    for st in m.SETTLED_ALERT_STATUSES:
        _alert(db, tid, f"email:{st}:x", status=st)
    _alert(db, tid, "email:open:x", status="open")
    db.commit()
    tenant = db.get(m.Tenant, tid)
    detail = sec.threat_detail(db, tenant, "phishing")
    db.close()

    assert len(detail.findings) == len(m.SETTLED_ALERT_STATUSES) + 1
    assert sum(1 for f in detail.findings if f.resolved) == len(m.SETTLED_ALERT_STATUSES)
    assert detail.open_count == 1


def test_a_finding_carries_the_why_and_the_evidence(client):
    """The card shows a title. The question "what did you find" is answered by
    the `why` and the evidence behind it, which is the reason to open this."""
    _uid, tid = seed_user()
    db = SessionLocal()
    _alert(db, tid, "email:spf:missing", msg="SPF missing",
           why="Nothing publishes an SPF record for this domain.",
           evidence={"domain": "example.com", "lookup": "no TXT"})
    db.commit()
    tenant = db.get(m.Tenant, tid)
    f = sec.threat_detail(db, tenant, "phishing").findings[0]
    db.close()
    assert "SPF record" in f.why
    assert f.evidence.get("domain") == "example.com"


def test_the_blocked_count_is_not_mixed_into_the_findings(client):
    """`blocked` is a seven-day DNS count from `DnsQuery`, credited per class
    through `THREAT_SPEC[*]['categories']`. It is not an alert count, and a
    drawer that added the two together would report a number that means
    nothing."""
    _uid, tid = seed_user()
    db = SessionLocal()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for i in range(3):
        db.add(m.DnsQuery(tenant_id=tid, ts=now, client="192.168.1.5",
                          name=f"bad{i}.example", qtype="A", blocked=True,
                          list="stevenblack", ms=1))
    _alert(db, tid, "email:spf:missing")
    db.commit()
    tenant = db.get(m.Tenant, tid)
    d = sec.threat_detail(db, tenant, "phishing")
    db.close()
    assert len(d.findings) == 1
    assert d.blocked >= 0
    assert d.blocked != len(d.findings) or d.blocked == 1


def test_the_admission_survives_into_the_detail(client):
    """`dos` is `effect-only` and `ransomware` is `indirect`: Vault does not
    stop either. `ThreatMatrix`'s docstring calls that admission the feature,
    and a drawer that dropped `limit` would turn the honest card into a
    misleading popup."""
    _uid, tid = seed_user()
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    dos = sec.threat_detail(db, tenant, "dos")
    ransom = sec.threat_detail(db, tenant, "ransomware")
    db.close()
    assert dos.stance == "effect-only" and dos.limit
    assert ransom.stance == "indirect" and ransom.limit


def test_an_unknown_class_is_refused_rather_than_invented(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    try:
        assert sec.threat_detail(db, tenant, "not-a-class") is None
    finally:
        db.close()
