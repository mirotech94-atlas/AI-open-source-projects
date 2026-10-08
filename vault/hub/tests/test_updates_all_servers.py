"""Item 30: every server Vault knows appears in Updates.

It read the latest scan's `ssh` block alone, so a server the sweep could
not reach — unreachable, credentials changed, or dropped out of the
sensor's config — was absent from the table. Absent reads as fine.
"""
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.payloads import updates as updates_payload
from tests.test_auth import seed_user


def _hosts(tid):
    db = SessionLocal()
    try:
        tenant = db.query(m.Tenant).filter_by(id=tid).one()
        return updates_payload.hosts(db, tenant)
    finally:
        db.close()


def test_a_server_the_sweep_missed_is_listed_as_unchecked(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(m.Scan(tenant_id=tid, ts="2026-09-21T10:00:00-04:00", score=90, health={},
                  raw={"ssh": [{"name": "vps1", "updates": {"count": 4}}]}))
    db.add(m.Host(tenant_id=tid, name="vps1", address="root@1.2.3.4", last={}))
    db.add(m.Host(tenant_id=tid, name="forgotten", address="root@5.6.7.8", last={}))
    db.commit(); db.close()

    rows = {h.name: h for h in _hosts(tid)}
    assert set(rows) == {"vps1", "forgotten"}
    assert rows["vps1"].checked is True and rows["vps1"].pending == 4
    missed = rows["forgotten"]
    assert missed.checked is False, "an unreached server must not read as a checked one"
    assert missed.pending == 0 and missed.security_pending == 0, \
        "no counts, because Guardian has none — the UI renders these as 'unknown'"


def test_a_scan_that_reached_everything_marks_nothing_unchecked(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(m.Scan(tenant_id=tid, ts="2026-09-21T10:00:00-04:00", score=90, health={},
                  raw={"ssh": [{"name": "vps1", "updates": {"count": 0}}]}))
    db.add(m.Host(tenant_id=tid, name="vps1", address="root@1.2.3.4", last={}))
    db.commit(); db.close()
    rows = _hosts(tid)
    assert len(rows) == 1 and rows[0].checked is True


def test_a_server_with_nothing_pending_is_still_listed(client):
    """"even if they dont need update because maybe they do" — a server with
    a clean bill of health still has to be visible to be trusted."""
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(m.Scan(tenant_id=tid, ts="2026-09-21T10:00:00-04:00", score=90, health={},
                  raw={"ssh": [{"name": "tidy", "updates": {"count": 0}}]}))
    db.commit(); db.close()
    assert [h.name for h in _hosts(tid)] == ["tidy"]


def test_patch_posture_counts_what_it_can_read_and_names_what_it_cannot(client):
    """Item 29: a total that silently skipped the phones and cameras would
    imply an all-clear Vault has not earned."""
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(m.Scan(tenant_id=tid, ts="2026-09-21T10:00:00-04:00", score=90, health={}, raw={
        "ssh": [
            {"name": "vps1", "updates": {"count": 4}, "sec": {"security_updates": ["openssl"]},
             "reboot_required": True},
            {"name": "vps2", "updates": {"count": 1}, "sec": {}},
        ],
        "mac_updates": {"os": "macOS", "available": ["Safari"], "ts": "2026-09-21T10:00:00-04:00"},
    }))
    db.add(m.Host(tenant_id=tid, name="vps1", address="root@1.2.3.4", last={}))
    db.add(m.Host(tenant_id=tid, name="vps2", address="root@1.2.3.5", last={}))
    db.add(m.Host(tenant_id=tid, name="unreachable", address="root@1.2.3.6", last={}))
    for i in range(3):
        db.add(m.Device(tenant_id=tid, mac=f"aa:bb:cc:00:00:0{i}"))
    db.commit(); db.close()

    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(id=tid).one()
    p = updates_payload.patch_posture(db, tenant)
    db.close()

    assert p.pending == 6, "4 + 1 on the servers, plus the sensor's own machine"
    assert p.security_pending == 1
    assert p.needs_reboot == 1
    assert p.servers_checked == 2
    assert p.servers_unchecked == 1, "the unreached server is counted, not dropped"
    assert p.unmeasured == 3, "devices with no readable package list are named, not skipped"


def test_an_unreached_server_contributes_no_counts(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(m.Scan(tenant_id=tid, ts="2026-09-21T10:00:00-04:00", score=90, health={}, raw={"ssh": []}))
    db.add(m.Host(tenant_id=tid, name="unreachable", address="root@1.2.3.6", last={}))
    db.commit(); db.close()
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(id=tid).one()
    p = updates_payload.patch_posture(db, tenant)
    db.close()
    assert (p.pending, p.security_pending, p.needs_reboot) == (0, 0, 0)
    assert p.servers_checked == 0 and p.servers_unchecked == 1
