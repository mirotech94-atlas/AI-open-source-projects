"""Item 20: an unwatched network and a quiet one are different answers.

They rendered identically -- "Vault cannot see traffic on this network"
-- which reads as a hardware limit even when the real answer is that nobody
has switched it on. Traffic capture is opt-in per network on Miro's own
ruling, so "off" is a normal state and the page has to be able to say so.
"""
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.payloads import traffic
from tests.test_seed_helpers import seeded_demo


def _net(db, tid, mode):
    n = db.query(m.Network).filter_by(tenant_id=tid).first()
    n.traffic_mode = mode
    db.commit()
    return n


def _why(tid, mode, capture=None):
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(id=tid).one()
    net = _net(db, tid, mode)
    if capture is not None:
        # The scan `mode_for` will actually read -- the demo seeds 30 days of
        # them and the highest id is not the latest ts.
        from guardian_hub.scans import latest_scan
        scan = latest_scan(db, tid)
        scan.raw = {**(scan.raw or {}), "capture": capture}
        db.commit()
    out = traffic.why_no_traffic(db, tenant, net)
    db.close()
    return out


def test_a_network_nobody_asked_about_says_so(client):
    tid = seeded_demo(client)
    why = _why(tid, "none")
    assert "not watching" in why and "nobody has turned it on" in why


def test_switched_on_but_no_interface_names_the_missing_piece(client):
    tid = seeded_demo(client)
    why = _why(tid, "mirror", {"mode": "off", "error": "", "interface": ""})
    assert "no capture interface" in why


def test_switched_on_with_neither_tool_installed_says_which_two(client):
    tid = seeded_demo(client)
    why = _why(tid, "mirror",
               {"mode": "none", "error": "neither tcpdump nor tshark is installed on the sensor"})
    assert "tcpdump" in why and "tshark" in why


def test_a_network_guardian_IS_watching_gets_no_excuse(client):
    """Then "nothing notable this hour" is a true and useful sentence, and
    the panel should be allowed to say it without a caveat."""
    tid = seeded_demo(client)
    assert _why(tid, "mirror", {"mode": "mirror", "error": "", "interface": "eth1"}) == ""


def test_off_collapses_to_none_so_every_existing_reader_is_unchanged(client):
    """The sensor's new word must not leak into the three-mode vocabulary
    the rest of the app reads."""
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(id=tid).one()
    net = _net(db, tid, "mirror")
    from guardian_hub.scans import latest_scan
    scan = latest_scan(db, tid)
    scan.raw = {**(scan.raw or {}), "capture": {"mode": "off", "error": "", "interface": ""}}
    db.commit()
    mode, _ = traffic.mode_for(db, tenant, net)
    db.close()
    assert mode == "none"
