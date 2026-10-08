"""Trusting or naming the last unverified device closes the network's
"not verified" alert right away, instead of minutes later on the sensor's
next sweep -- the inbox row the person is looking at goes green as they
watch."""
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.routers.write import apply_device_update, settle_unverified_alert

from tests.test_seed_helpers import seeded_demo


def _setup(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    t = db.get(m.Tenant, tid)
    net = db.query(m.Network).filter_by(tenant_id=tid).first()
    # two unverified devices on the network, one open unverified alert
    for d in db.query(m.Device).filter_by(tenant_id=tid, network_id=net.id).all():
        d.trusted = True
    a1 = m.Device(tenant_id=tid, network_id=net.id, mac="aa:11:11:11:11:11", ip="10.0.0.11", name="tv", trusted=False, label="")
    a2 = m.Device(tenant_id=tid, network_id=net.id, mac="aa:22:22:22:22:22", ip="10.0.0.12", name="cast", trusted=False, label="")
    db.add_all([a1, a2])
    key = f"lan:{net.slug}:unverified"
    db.query(m.Alert).filter_by(tenant_id=tid, key=key).delete()
    now = "2026-09-14T22:00:00-04:00"
    db.add(m.Alert(tenant_id=tid, network_id=net.id, key=key, sev="WARN", msg="2 device(s) not verified", status="open",
                   first_seen=now, last_seen=now))
    # The scan names exactly the devices its alert is about -- the sensor's
    # own list, which the dashboard's unverified count already reads.
    from guardian_hub.scans import latest_scan
    scan = latest_scan(db, tid)
    raw = dict(scan.raw or {})
    raw["home"] = {**(raw.get("home") or {}), "unverified": [
        {"mac": "aa:11:11:11:11:11"}, {"mac": "aa:22:22:22:22:22"}]}
    scan.raw = raw
    db.commit()
    return db, t, net, key


def test_alert_stays_while_one_device_is_still_unverified(client):
    db, t, net, key = _setup(client)
    apply_device_update(db, t, "aa:11:11:11:11:11", {"trusted": True}, who="ann", role="admin")
    assert db.query(m.Alert).filter_by(tenant_id=t.id, key=key).one().status == "open"


def test_trusting_the_last_one_resolves_the_alert_live(client):
    db, t, net, key = _setup(client)
    apply_device_update(db, t, "aa:11:11:11:11:11", {"trusted": True}, who="ann", role="admin")
    apply_device_update(db, t, "aa:22:22:22:22:22", {"trusted": True}, who="ann", role="admin")
    a = db.query(m.Alert).filter_by(tenant_id=t.id, key=key).one()
    assert a.status == "resolved"


def test_naming_a_device_counts_as_verifying_it(client):
    db, t, net, key = _setup(client)
    apply_device_update(db, t, "aa:11:11:11:11:11", {"trusted": True}, who="ann", role="admin")
    apply_device_update(db, t, "aa:22:22:22:22:22", {"label": "Kitchen speaker"}, who="ann", role="admin")
    assert db.query(m.Alert).filter_by(tenant_id=t.id, key=key).one().status == "resolved"


def test_settle_is_a_noop_without_an_open_alert(client):
    db, t, net, key = _setup(client)
    db.query(m.Alert).filter_by(tenant_id=t.id, key=key).delete(); db.commit()
    d = db.query(m.Device).filter_by(tenant_id=t.id, mac="aa:11:11:11:11:11").one()
    d.trusted = True
    d2 = db.query(m.Device).filter_by(tenant_id=t.id, mac="aa:22:22:22:22:22").one()
    d2.trusted = True
    db.commit()
    assert settle_unverified_alert(db, t, d, who="ann") is False



def test_an_old_offline_device_the_alert_never_named_does_not_hold_it_open(client):
    """Production, 2026-09-28: the owner trusted the one iPhone the alert
    named, and the alert stayed. The check counted every untrusted,
    unnamed row the hub had EVER seen on the network -- including an iPhone
    that had been offline for days and was nowhere in the alert. It has to
    ask about the devices the scan flagged, as the dashboard's count does."""
    db, t, net, key = _setup(client)
    db.add(m.Device(tenant_id=t.id, network_id=net.id, mac="aa:99:99:99:99:99", ip="10.0.0.99",
                    name="iphone", trusted=False, label="", presence="OFFLINE"))
    db.commit()
    apply_device_update(db, t, "aa:11:11:11:11:11", {"trusted": True}, who="ann", role="admin")
    apply_device_update(db, t, "aa:22:22:22:22:22", {"label": "Baba iPhone", "trusted": True}, who="ann", role="admin")
    assert db.query(m.Alert).filter_by(tenant_id=t.id, key=key).one().status == "resolved"


def test_the_verify_devices_recommendation_goes_with_the_alert(client):
    """The red "Verify N unknown device(s)" card is the sensor's
    `lan:unverified` recommendation, which only ever cleared on the next
    sweep. It is about exactly what the owner just settled, so it goes now;
    if the sensor still has a reason for it, its next sweep raises it again."""
    db, t, net, key = _setup(client)
    db.query(m.Recommendation).filter_by(tenant_id=t.id, key="lan:unverified").delete()
    db.add(m.Recommendation(tenant_id=t.id, key="lan:unverified", title="Verify 2 unknown device(s) on the Wi-Fi", priority="High", last_seen="2026-09-14T22:00:00-04:00",
                            decision="yes", status="needs-you"))
    db.commit()
    apply_device_update(db, t, "aa:11:11:11:11:11", {"trusted": True}, who="ann", role="admin")
    assert db.query(m.Recommendation).filter_by(tenant_id=t.id, key="lan:unverified").count() == 1
    apply_device_update(db, t, "aa:22:22:22:22:22", {"trusted": True}, who="ann", role="admin")
    assert db.query(m.Recommendation).filter_by(tenant_id=t.id, key="lan:unverified").count() == 0
