"""Task 31: the `/topology` payload -- internet/router/extender/device nodes
plus one server node per Host and one site node per Site, all reachable
from `internet` so the client can draw the graph with no orphans."""

import time

from tests.test_seed_helpers import seeded_demo


def test_topology_is_a_connected_graph_the_ui_can_draw(client):
    seeded_demo(client)
    t = client.get("/api/v1/topology").json()
    ids = {n["id"] for n in t["nodes"]}
    assert "internet" in ids and any(n["kind"] == "router" for n in t["nodes"])
    assert all(l["source"] in ids and l["target"] in ids for l in t["links"])
    assert all(n["ring"] in (0, 1, 2, 3) for n in t["nodes"])
    assert all(n["icon"] for n in t["nodes"])
    reachable, frontier = {"internet"}, ["internet"]
    adj = {}
    for l in t["links"]:
        adj.setdefault(l["source"], []).append(l["target"]); adj.setdefault(l["target"], []).append(l["source"])
    while frontier:
        n = frontier.pop()
        for x in adj.get(n, []):
            if x not in reachable: reachable.add(x); frontier.append(x)
    assert reachable == ids, f"orphans: {sorted(ids - reachable)}"


def test_cluster_by_area_falls_back_to_one_cluster(client):
    seeded_demo(client)
    c = client.get("/api/v1/topology?cluster_by=area").json()["clusters"]
    assert c and sum(len(x["members"]) for x in c) >= 20


def test_cluster_by_kind_groups_router_and_extenders_into_network_gear(client):
    seeded_demo(client)
    c = client.get("/api/v1/topology?cluster_by=kind").json()["clusters"]
    network_gear = next(x for x in c if x["id"] == "Network gear")
    assert len(network_gear["members"]) >= 2


def test_empty_tenant_still_returns_a_connected_internet_and_router(client):
    """No devices at all (no scan, so no gw_mac) -- the builder must
    synthesize a router node rather than leaving the graph without a
    gateway, so it stays fully connected even for a brand-new tenant."""
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    from guardian_hub.payloads import topology as topology_payload

    db = SessionLocal()
    tenant = m.Tenant(slug="empty-topo", name="Empty Topo")
    db.add(tenant)
    db.commit()

    t = topology_payload.build(db, tenant)
    ids = {n.id for n in t.nodes}
    assert ids == {"internet", "router"}
    assert [(l.source, l.target, l.kind, l.pulse) for l in t.links] == [("router", "internet", "wan", False)]


def test_topology_answers_quickly_on_the_demo_tenant(client):
    seeded_demo(client)
    t0 = time.monotonic()
    assert client.get("/api/v1/topology").status_code == 200
    assert time.monotonic() - t0 < 2.0


def test_topology_carries_the_per_network_strip(client):
    """Round 2 of Task 51: `TopologyPayload.networks` is the same
    `NetworkHealth` strip `DevicesPayload`/`Overview` already carry, so the
    web topology view can render the same network chips without a second
    request."""
    seeded_demo(client)
    t = client.get("/api/v1/topology").json()
    # Office Wi-Fi + Lab, plus the demo's own recorder's camera network
    # (Task 83's camera census fixture) -- unswept, but still a real network.
    assert {"office-wi-fi", "lab"} <= {n["slug"] for n in t["networks"]}
    prim = next(n for n in t["networks"] if n["is_primary"])
    assert prim["slug"] == "office-wi-fi"
    assert all("is_primary" in n for n in t["networks"])
