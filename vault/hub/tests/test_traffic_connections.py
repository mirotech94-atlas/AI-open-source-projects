"""Live connections, grouped and named for a person.

The owner's verdict on the raw stream was "I don't really understand what I
am seeing". These pin the reading: devices, what each talked to by name,
what kind of traffic it was, and which destinations are new.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import services, traffic
from tests.test_seed_helpers import seeded_demo


def _flow(db, tid, net_id, mac, peer_ip, port, proto, sni="", minutes_ago=2, bytes_in=1000):
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    db.add(m.Flow(tenant_id=tid, network_id=net_id, ts=ts, mac=mac, ip="192.168.1.20", peer_ip=peer_ip,
                  peer_port=port, proto=proto, bytes_in=bytes_in, bytes_out=100, pkts_in=1, pkts_out=1,
                  peer_name="", hints={"sni": sni} if sni else {}, source="mirror"))


def test_services_are_named_only_when_the_hostname_says_so():
    assert services.identify("api.anthropic.com", "160.79.104.10") == ("Anthropic", "AI service", True)
    assert services.identify("r3.sn-abc.googlevideo.com", "1.2.3.4")[0] == "YouTube"   # longest suffix wins
    assert services.identify("", "203.0.113.9") == ("203.0.113.9", "an address on the internet", False)
    assert services.identify("", "192.168.1.1")[1] == "a device on your network"
    assert services.identify("unknown.example", "") == ("unknown.example", "", False)


def test_ports_read_as_what_they_are_for():
    assert services.port_meaning("tcp", 443) == "Encrypted web"
    assert services.port_meaning("udp", 123) == "Clock sync"
    assert services.port_meaning("tcp", 31337) == "TCP 31337"


def test_a_device_groups_its_destinations_and_marks_the_new_one(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    t = db.get(m.Tenant, tid)
    for n in db.query(m.Network).filter_by(tenant_id=tid).all():
        n.traffic_mode = "mirror"
    dev = db.query(m.Device).filter_by(tenant_id=tid).first()
    mac = "aa:bb:cc:dd:ee:77"
    # Anthropic seen yesterday, so not new; the second one never before.
    _flow(db, tid, dev.network_id, mac, "160.79.104.10", 443, "tcp", sni="api.anthropic.com", minutes_ago=60 * 20)
    _flow(db, tid, dev.network_id, mac, "160.79.104.10", 443, "tcp", sni="api.anthropic.com", bytes_in=5000)
    _flow(db, tid, dev.network_id, mac, "198.51.100.77", 443, "tcp", minutes_ago=1)
    db.commit()
    out = traffic.connections(db, t, minutes=15)
    row = next(d for d in out.devices if d.mac == mac)
    by_name = {d.name: d for d in row.destinations}
    assert by_name["Anthropic"].what == "AI service" and by_name["Anthropic"].sure
    assert by_name["Anthropic"].port_meaning == "Encrypted web"
    assert not by_name["Anthropic"].is_new
    assert by_name["198.51.100.77"].is_new, "a first-time destination must be marked"
    assert row.destinations[0].name == "Anthropic", "biggest first"
    db.close()


def test_the_route_serves_it(client):
    seeded_demo(client)
    r = client.get("/api/v1/traffic/connections?minutes=15")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["window_minutes"] == 15 and "devices" in body
