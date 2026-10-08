"""`host`: the sensor captures only its own traffic, and Vault says so.

The real network this was built for is declared `mirror`, but its sensor is a
Mac on Wi-Fi. Wi-Fi gives a capturing host its own connections plus
broadcasts -- never another device's -- so turning capture on under the
`mirror` label would have put one machine's traffic on a page that claims to
show the whole network's, with destinations and "new peers" presented as if
everyone's. Miro chose the honest label instead.

The sensor still reports `mirror` when it is capturing: it cannot know
whether the port it listens on is a mirror. As with every other mode, the
person's DECLARATION says what the rows can answer (`payloads/traffic.
mode_for`), and `host` is that declaration.
"""
from guardian_hub import models as m, networks
from guardian_hub.chat import surfaces
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import traffic
from guardian_hub.scans import latest_scan
from tests.test_seed_helpers import seeded_demo


def _declare(tid, mode, capture):
    db = SessionLocal()
    for n in db.query(m.Network).filter_by(tenant_id=tid).all():
        n.traffic_mode = mode
    scan = latest_scan(db, tid)
    scan.raw = {**(scan.raw or {}), "capture": capture}
    db.commit()
    return db, db.query(m.Tenant).filter_by(id=tid).one()


CAPTURING = {"mode": "mirror", "error": "", "interface": "en0", "flows_dropped": 0}


def test_host_is_a_mode_a_person_can_declare():
    assert "host" in networks.TRAFFIC_MODES


def test_a_capturing_sensor_under_a_host_declaration_reads_as_host(client):
    tid = seeded_demo(client)
    db, tenant = _declare(tid, "host", CAPTURING)
    assert traffic.mode_for(db, tenant, None)[0] == "host"
    assert traffic.why_no_traffic(db, tenant, None) == ""
    db.close()


def test_host_without_a_capture_interface_is_none_with_the_reason(client):
    tid = seeded_demo(client)
    db, tenant = _declare(tid, "host", {"mode": "off", "error": "", "interface": ""})
    assert traffic.mode_for(db, tenant, None)[0] == "none"
    assert "no capture interface" in traffic.why_no_traffic(db, tenant, None)
    db.close()


def test_the_assistant_says_it_is_one_machine_not_the_network(client):
    tid = seeded_demo(client)
    db, tenant = _declare(tid, "host", CAPTURING)
    opener = surfaces._mode_preamble("host")
    assert "sensor" in opener and "only" in opener
    assert "network" in opener  # names what it CANNOT see
    db.close()


def test_the_patch_route_accepts_host(client):
    seeded_demo(client)
    nets = client.get("/api/v1/networks").json()
    rows = nets["networks"] if isinstance(nets, dict) else nets
    r = client.patch(f"/api/v1/networks/{rows[0]['id']}", json={"traffic_mode": "host"})
    assert r.status_code == 200, r.text
