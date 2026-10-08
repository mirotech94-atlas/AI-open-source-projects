"""Task 70: the nine Devices summary tiles.

They are built against the 30-day demo tenant (two networks, one sensor), so
the "All networks" / per-network split is exercised on real seeded data rather
than a hand-rolled row.
"""

import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def _tiles(client, network=None):
    q = f"?network={network}" if network else ""
    r = client.get(f"/api/v1/devices{q}")
    assert r.status_code == 200, r.text
    return r.json()


def test_every_tile_is_backed_by_ingested_data(client):
    seeded_demo(client)
    body = _tiles(client)
    t = body["tiles"]
    # Devices online: the payload's own counts, not a tile field.
    assert 0 < body["online"] <= body["total"]
    # Counts really are counts -- 0 is a fact about the network.
    assert isinstance(t["new_devices"], int) and t["new_devices"] >= 0
    assert 0 < t["trusted"] <= body["total"]
    assert 0 <= t["randomized"] <= body["total"]
    # The fixture's own speed sample and resolver timing, read straight off the
    # scan: 526.6/86.3 Mbps, 21 ms, system resolver ok in 96 ms.
    assert t["down_mbps"] == 526.6 and t["up_mbps"] == 86.3
    assert t["latency_ms"] == 21
    assert t["dns_ms"] == 96 and t["dns_ok"] is True
    assert t["wan_ip"] and t["wan_ips"] == [t["wan_ip"]]


def test_randomized_count_is_the_locally_administered_bit(client):
    seeded_demo(client)
    body = _tiles(client)
    cards = [c for g in body["groups"] for c in g["devices"]]
    expected = sum(1 for c in cards if int(c["mac"].split(":")[0], 16) & 2)
    assert body["tiles"]["randomized"] == expected
    assert sum(1 for c in cards if c["randomized"]) == expected


def test_tiles_follow_the_selected_network(client):
    seeded_demo(client)
    nets = client.get("/api/v1/networks").json()["networks"]
    ids = {n["slug"]: n["id"] for n in nets}
    everywhere = _tiles(client)["tiles"]
    per = {slug: _tiles(client, nid)["tiles"] for slug, nid in ids.items()}
    # Device-shaped counts partition across the networks; the host-shaped
    # internet figures are the same machine's measurement either way.
    assert sum(p["trusted"] for p in per.values()) == everywhere["trusted"]
    assert sum(p["randomized"] for p in per.values()) == everywhere["randomized"]
    for p in per.values():
        assert p["down_mbps"] == everywhere["down_mbps"]
        assert p["dns_ms"] == everywhere["dns_ms"]


def test_one_sensor_means_one_wan_address_however_many_networks(client):
    """The demo watches two networks through one sensor, so its egress -- and
    therefore its WAN tile -- is a single address under "All networks"."""
    seeded_demo(client)
    nets = client.get("/api/v1/networks").json()["networks"]
    everywhere = _tiles(client)["tiles"]
    assert len(everywhere["wan_ips"]) == 1 and everywhere["wan_ip"] == everywhere["wan_ips"][0]
    for n in nets:
        scoped = _tiles(client, n["id"])["tiles"]
        # The Lab has no sensor of its own, so it falls back to the tenant's
        # latest scan rather than reporting no address at all.
        assert scoped["wan_ips"] == everywhere["wan_ips"]


def test_two_sensors_with_different_egress_report_a_count_not_an_address(client):
    """A second network with its own sensor and its own WAN address: the
    unscoped tile cannot name one address, so it carries both and leaves
    `wan_ip` empty for the UI to render as "2 addresses"."""
    seeded_demo(client)
    nets = client.get("/api/v1/networks").json()["networks"]
    lab = next(n for n in nets if not n["is_primary"])
    db = SessionLocal()
    try:
        tenant = db.query(m.Tenant).filter_by(slug="demo").first()
        second = m.Sensor(tenant_id=tenant.id, name="lab-sensor", status="active",
                          last_seen=dt.datetime.now(dt.timezone.utc))
        db.add(second)
        db.flush()
        db.add(m.Scan(tenant_id=tenant.id, ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                      sensor_id=second.id, raw={"wan_ip": "203.0.113.9"}))
        db.query(m.Network).filter_by(id=lab["id"]).update({"sensor_id": second.id})
        db.commit()
    finally:
        db.close()

    everywhere = _tiles(client)["tiles"]
    assert "203.0.113.9" in everywhere["wan_ips"] and len(everywhere["wan_ips"]) == 2
    assert everywhere["wan_ip"] == ""
    # Scoped to the lab, the address is that sensor's own and nameable again.
    scoped = _tiles(client, lab["id"])["tiles"]
    assert scoped["wan_ip"] == "203.0.113.9" and scoped["wan_ips"] == ["203.0.113.9"]


# --------------------------------------------------------------------------
# Task 77 / F5: the three device trend panels
# --------------------------------------------------------------------------

def test_devices_payload_carries_the_three_trend_panels(client):
    from guardian_hub.payloads import assets as assets_payload
    from tests.test_seed_helpers import seeded_demo

    seeded_demo(client)
    body = client.get("/api/v1/devices").json()
    keys = [c["key"] for c in body["charts"]]
    assert keys == ["devices_online", "devices_joins", "devices_known"]
    assert assets_payload.DEVICE_TREND_DAYS == 7

    joins = next(c for c in body["charts"] if c["key"] == "devices_joins")
    assert [s["name"] for s in joins["series"]] == ["joined", "left"]
    for series in joins["series"]:
        # one point per day for the whole window, oldest first, zeroes included
        assert len(series["points"]) == assets_payload.DEVICE_TREND_DAYS
        stamps = [p["ts"] for p in series["points"]]
        assert stamps == sorted(stamps)
        assert all(p["value"] >= 0 for p in series["points"])


def test_online_vs_known_never_claims_more_seen_than_known(client):
    """"Known" is how many devices Vault had already met by that day, so the
    number seen that day can never exceed it."""
    from tests.test_seed_helpers import seeded_demo

    seeded_demo(client)
    chart = next(c for c in client.get("/api/v1/devices").json()["charts"]
                 if c["key"] == "devices_known")
    known = {p["ts"]: p["value"] for p in chart["series"][0]["points"]}
    seen = {p["ts"]: p["value"] for p in chart["series"][1]["points"]}
    assert known and seen.keys() == known.keys()
    for day, value in seen.items():
        assert value <= known[day], day
    # known is cumulative, so it never decreases
    ordered = [known[d] for d in sorted(known)]
    assert ordered == sorted(ordered)


def test_the_joins_chart_is_scoped_by_the_network_filter(client):
    from tests.test_seed_helpers import seeded_demo

    seeded_demo(client)
    nets = client.get("/api/v1/networks").json()["networks"]

    def joins(payload):
        chart = next((c for c in payload["charts"] if c["key"] == "devices_joins"), None)
        if chart is None:
            return {}
        return {p["ts"]: p["value"] for p in chart["series"][0]["points"]}

    whole = joins(client.get("/api/v1/devices").json())
    one = joins(client.get(f"/api/v1/devices?network={nets[0]['slug']}").json())
    for day, value in one.items():
        assert value <= whole.get(day, 0)


def test_a_tenant_with_no_devices_gets_no_panels(client):
    from tests.test_auth import login, seed_user

    seed_user(username="ann", email="a@x.com", tenant_slug="empty2", role="viewer")
    login(client, "ann", "a@x.com")
    assert client.get("/api/v1/devices").json()["charts"] == []
