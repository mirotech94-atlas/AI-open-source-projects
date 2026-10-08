import datetime as dt

from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.payloads import traffic
from guardian_hub.scans import latest_scan
from tests.test_seed_helpers import seeded_demo
from tests.querycount import queries_for


def _flow(db, t, mac, peer_ip, bi, bo, minutes_ago=5, name="", offset="+00:00"):
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)).replace(microsecond=0)
    if offset != "+00:00":   # same instant, written in another zone -- the lexicographic trap
        ts = ts.astimezone(dt.timezone(dt.timedelta(hours=int(offset[:3]))))
    db.add(m.Flow(tenant_id=t.id, ts=ts.isoformat(), mac=mac, ip="192.168.1.20", peer_ip=peer_ip, peer_port=443,
                  proto="tcp", bytes_in=bi, bytes_out=bo, pkts_in=1, pkts_out=1, peer_name=name, hints={}))


def _tenant(): db = SessionLocal(); return db, db.query(m.Tenant).filter_by(slug="demo").one()


def test_mode_is_none_when_no_network_declares_a_mirror(client):
    seeded_demo(client); db, t = _tenant()
    # Task 14 makes `seed_demo` declare the primary network `mirror` (the
    # demo has traffic to show, on purpose) -- so this test's own premise
    # ("no network declares a mirror") now has to be set up explicitly
    # rather than relying on the seeded tenant's default.
    net = db.query(m.Network).filter_by(tenant_id=t.id, is_primary=True).one()
    net.traffic_mode = "none"; db.commit()
    assert traffic.overview(db, t).mode == "none"


def test_top_talkers_and_peers_by_bytes_with_names_over_ips(client):
    seeded_demo(client); db, t = _tenant()
    net = db.query(m.Network).filter_by(tenant_id=t.id, is_primary=True).one(); net.traffic_mode = "mirror"
    # The demo fixture predates the capture pipeline (Task 14 is what makes
    # `seed_demo` push `last["capture"]`), so it carries no `capture` block
    # of its own. `mode_for` reads the sensor's *reported* mode from the
    # latest scan's raw payload, never the bare declaration -- a network
    # can say "mirror" while the sensor still says nothing -- so a test
    # about bytes-per-device has to declare the mirror actually live.
    s = latest_scan(db, t.id)
    s.raw = {**(s.raw or {}), "capture": {"mode": "mirror"}}
    # Task 14 also seeds ~600 `Flow` rows of its own (`seed._gen_flows`), and
    # with the fixed seed (`random.Random(20260909)`) some of them land in
    # the last hour with byte counts above this test's fixed 5100 -- a
    # deterministic fact about this fixture, not a coin flip, but one that
    # would break silently the day the fixture or `n=600` changes. Cleared
    # so "top by bytes" is asserted over exactly the two rows this test
    # itself adds.
    db.query(m.Flow).filter_by(tenant_id=t.id).delete()
    dev = db.query(m.Device).filter_by(tenant_id=t.id).first()
    _flow(db, t, dev.mac, "142.250.1.1", 5000, 100, name="www.google.com"); _flow(db, t, "aa:bb:cc:00:00:02", "1.1.1.1", 10, 10); db.commit()
    o = traffic.overview(db, t, hours=1)
    assert o.mode == "mirror" and o.top_talkers[0].mac == dev.mac and o.top_peers[0].peer == "www.google.com"


def test_a_row_in_another_offset_is_still_inside_the_window(client):
    seeded_demo(client); db, t = _tenant()
    _flow(db, t, "aa:bb:cc:00:00:03", "9.9.9.9", 1, 1, minutes_ago=5, offset="+05:00"); db.commit()
    d = traffic.device(db, t, "aa:bb:cc:00:00:03", hours=1)
    assert d.bytes_in == 1


def test_new_peers_are_those_unseen_in_the_previous_raw_window(client):
    seeded_demo(client); db, t = _tenant()
    _flow(db, t, "aa:bb:cc:00:00:04", "8.8.8.8", 1, 1, minutes_ago=3 * 24 * 60); _flow(db, t, "aa:bb:cc:00:00:04", "8.8.8.8", 1, 1)
    _flow(db, t, "aa:bb:cc:00:00:04", "5.5.5.5", 1, 1); db.commit()
    assert [p.peer for p in traffic.overview(db, t, hours=1).new_peers] == ["5.5.5.5"]


def test_low_byte_new_peer_is_not_hidden_by_a_top_talker_that_is_also_new(client):
    seeded_demo(client); db, t = _tenant()
    mac = "aa:bb:cc:00:00:05"
    # Nine peers this device talked to two hours ago (outside the 1h window,
    # inside the retention floor -- "seen before") and again just now, plus
    # one brand-new big peer: ten peers, exactly filling `out.top_peers` by
    # bytes. A new peer with only 1 byte must still surface in `new_peers`
    # even though it never makes that top-10-by-bytes cut -- the whole
    # reason `new_peers` has to scan every peer, not just the top talkers.
    for i in range(9):
        ip = f"10.0.0.{i}"
        _flow(db, t, mac, ip, 1000, 0, minutes_ago=120)   # seen before
        _flow(db, t, mac, ip, 1000, 0, minutes_ago=5)      # and again, now
    _flow(db, t, mac, "9.9.9.8", 2000, 0, minutes_ago=5)   # new, big
    _flow(db, t, mac, "9.9.9.9", 1, 0, minutes_ago=5)      # new, tiny
    db.commit()
    peers = [p.peer for p in traffic.overview(db, t, hours=1).new_peers]
    assert "9.9.9.8" in peers and "9.9.9.9" in peers


def test_a_peer_seen_twelve_hours_before_the_window_is_not_new(client):
    seeded_demo(client); db, t = _tenant()
    # Contacted half a day before the 1h window starts, then again inside
    # it. The old "seen before" query bounded its upper edge at the
    # window's OWN lax cutoff (window_start - 1 day), so anything in the
    # 24h immediately before the window -- including twelve hours ago --
    # fell through neither bound and was misreported as new.
    _flow(db, t, "aa:bb:cc:00:00:06", "4.4.4.4", 1, 1, minutes_ago=12 * 60)
    _flow(db, t, "aa:bb:cc:00:00:06", "4.4.4.4", 1, 1, minutes_ago=5)
    db.commit()
    assert "4.4.4.4" not in [p.peer for p in traffic.overview(db, t, hours=1).new_peers]


def test_statement_count_does_not_grow_with_devices(client):
    seeded_demo(client); db, t = _tenant()
    for i in range(3): _flow(db, t, f"aa:bb:cc:00:01:{i:02x}", "1.1.1.1", 1, 1)
    db.commit(); n3 = queries_for(client, "/api/v1/traffic?hours=1")
    for i in range(30): _flow(db, t, f"aa:bb:cc:00:02:{i:02x}", "1.1.1.1", 1, 1)
    db.commit(); n33 = queries_for(client, "/api/v1/traffic?hours=1")
    assert n3 == n33


def test_patching_a_network_to_mirror_changes_the_reported_mode(client):
    seeded_demo(client)
    db, t = _tenant(); net = db.query(m.Network).filter_by(tenant_id=t.id, is_primary=True).one()

    r = client.patch(f"/api/v1/networks/{net.id}", json={"traffic_mode": "mirror"})
    assert r.status_code == 200 and r.json()["traffic_mode"] == "mirror"
    # A session that never saw the pre-patch row, not `db` -- proves the PATCH
    # actually persisted to the row, not merely that the response echoed back
    # what was sent (a route that validated and returned 200 without writing
    # would pass the assertion above but not this one).
    fresh = SessionLocal()
    assert fresh.query(m.Network).filter_by(id=net.id).one().traffic_mode == "mirror"

    # The demo fixture predates the capture pipeline (Task 14 is what makes
    # `seed_demo` push `last["capture"]`), so it carries no `capture` block of
    # its own -- `mode_for` reads the sensor's *reported* mode from the latest
    # scan's raw payload, never the bare declaration, so a network can say
    # "mirror" while the sensor still says nothing. Stamp the scan the same
    # way `test_top_talkers_and_peers_by_bytes_with_names_over_ips` does, or
    # the GET below reads "none" whether or not the PATCH persisted -- exactly
    # the gap the previous version of this test missed (it asserted `mode in
    # ("mirror", "none")`, which passes even for a PATCH that silently no-ops).
    s = latest_scan(db, t.id)
    s.raw = {**(s.raw or {}), "capture": {"mode": "mirror", "error": "", "interface": "eth1", "flows_dropped": 0}}
    db.commit()
    assert client.get("/api/v1/traffic").json()["mode"] == "mirror"

    # A declared "none" is taken at its word even though the sensor still
    # reports mirror -- the other half of `mode_for`: the sensor's report only
    # wins when the network itself declares mirror or router.
    r = client.patch(f"/api/v1/networks/{net.id}", json={"traffic_mode": "none"})
    assert r.status_code == 200
    assert client.get("/api/v1/traffic").json()["mode"] == "none"

    r = client.patch(f"/api/v1/networks/{net.id}", json={"traffic_mode": "sideways"})
    assert r.status_code == 422
    assert r.json()["detail"] == "traffic_mode must be one of mirror, router, host, none"
