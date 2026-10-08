"""Coverage and the expiring-certificate list must read the key the sensor
writes. They read `cert_days`; the sensor writes `cert_days_left`. Every
site therefore counted as unread and the customer was told Vault had not
read a certificate for any of their sites, while it had read all three."""
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import overview as ov
from tests.test_auth import seed_user


def _site(tid, name, last):
    return m.Site(tenant_id=tid, name=name, url=f"https://{name}.example", last=last)


def _rows(tid):
    db = SessionLocal()
    try:
        t = db.get(m.Tenant, tid)
        cov = {r.label: r for r in ov.coverage_rows(db, tid, [])}
        return cov, ov.certs_expiring(db, tid)
    finally:
        db.close()


def test_a_certificate_the_sensor_wrote_counts_as_read(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add_all([_site(tid, "a", {"ok": True, "cert_days_left": 77, "cert_expires": "2026-12-07"}),
                _site(tid, "b", {"ok": True, "cert_days_left": 12, "cert_expires": "2026-10-03"}),
                _site(tid, "c", {"ok": True})])                     # genuinely unread
    db.commit(); db.close()
    cov, expiring = _rows(tid)
    assert (cov["Certificates"].seen, cov["Certificates"].total) == (2, 3)
    assert [c.days_left for c in expiring] == [12, 77] or 12 in [c.days_left for c in expiring]


def test_the_old_key_is_still_honoured(client):
    _uid, tid = seed_user()
    db = SessionLocal(); db.add(_site(tid, "old", {"ok": True, "cert_days": 40})); db.commit(); db.close()
    cov, _ = _rows(tid)
    assert cov["Certificates"].seen == 1
