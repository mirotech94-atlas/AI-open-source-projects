"""Task 51: several networks per tenant -- the demo's two networks, the
`?network=` filter on every scoped read, the overview health strip, the
same-MAC-on-two-networks case, CRUD validation/audit/whole-config-Edit, and
`slugify`."""

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import demoscrub, models as m, networks


def test_seed_makes_two_networks_with_real_subnets(client):
    seeded_demo(client)
    body = client.get("/api/v1/networks").json()["networks"]
    # Office Wi-Fi + Lab (the sensor-swept LAN networks), plus the demo's own
    # recorder's camera network (Task 83's camera census fixture) -- hub-side
    # only, never swept, but still a real `Network` row every listing sees.
    assert {"Office Wi-Fi", "Lab"} <= {n["name"] for n in body}
    assert "Clinic NVR cameras" in {n["name"] for n in body}
    prim = next(n for n in body if n["is_primary"])
    assert prim["slug"] == "office-wi-fi" and prim["subnet_prefix"] == "192.168.1"
    lab = next(n for n in body if n["name"] == "Lab")
    assert lab["slug"] == "lab" and lab["subnet_prefix"] == "10.10.0" and lab["devices"] >= 6
    db = SessionLocal()
    for d in db.query(m.Device).filter_by(network_id=lab["id"]).all():
        assert d.ip.startswith("10.10.0."), d.ip


def test_every_scoped_route_filters_by_network(client):
    seeded_demo(client)
    nets = client.get("/api/v1/networks").json()["networks"]
    ids = {n["slug"]: n["id"] for n in nets}
    total = client.get("/api/v1/devices").json()["total"]
    per = {s: client.get(f"/api/v1/devices?network={i}").json()["total"] for s, i in ids.items()}
    assert sum(per.values()) == total and all(v > 0 for v in per.values())
    for path in ("/api/v1/inbox?view=alerts&", "/api/v1/events?", "/api/v1/topology?",
                 "/api/v1/dns?", "/api/v1/presence?days=7&"):
        r = client.get(f"{path}network={ids['lab']}")
        assert r.status_code == 200, path
    topo = client.get(f"/api/v1/topology?network={ids['lab']}").json()
    assert all(n["network"] is None or n["network"]["slug"] == "lab" for n in topo["nodes"])
    assert client.get("/api/v1/devices?network=nope").status_code == 404


def test_overview_carries_a_health_strip_per_network(client):
    seeded_demo(client)
    strip = client.get("/api/v1/overview").json()["networks"]
    # Office Wi-Fi + Lab, plus the demo's own recorder's camera network
    # (Task 83's camera census fixture)
    assert len(strip) == 3
    assert all(s["state"] in ("good", "warn", "bad") and s["status"] in
               ("active", "pending", "unreachable", "no-sensor") for s in strip)
    assert sum(s["devices"] for s in strip) == client.get("/api/v1/devices").json()["total"]


def test_the_same_mac_on_two_networks_is_two_devices(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    nets = {n.slug: n for n in db.query(m.Network).filter_by(tenant_id=tid).all()}
    mac = demoscrub.map_mac("74:04:f1:4f:5a:ca")
    db.add(m.Device(tenant_id=tid, network_id=nets["lab"].id, mac=mac, ip="10.10.0.159",
                    presence="ONLINE", last_seen="2026-09-09T18:30:26-04:00"))
    db.commit()
    assert db.query(m.Device).filter_by(tenant_id=tid, mac=mac).count() == 2
    d = client.get(f"/api/v1/devices/{mac}?network={nets['lab'].id}").json()["device"]
    assert d["ip"] == "10.10.0.159" and d["network"]["slug"] == "lab"


def test_a_mac_on_two_networks_with_no_filter_picks_the_newest(client):
    """Not an arbitrary `.first()`: the newest `last_seen` row wins, and the
    response says which network it came from."""
    tid = seeded_demo(client)
    db = SessionLocal()
    nets = {n.slug: n for n in db.query(m.Network).filter_by(tenant_id=tid).all()}
    mac = demoscrub.map_mac("74:04:f1:4f:5a:ca")
    db.add(m.Device(tenant_id=tid, network_id=nets["lab"].id, mac=mac, ip="10.10.0.159",
                    presence="ONLINE", last_seen="2099-01-01T00:00:00+00:00"))
    db.commit()
    d = client.get(f"/api/v1/devices/{mac}").json()["device"]
    assert d["network"]["slug"] == "lab" and d["ip"] == "10.10.0.159"


def test_crud_validates_audits_and_ships_the_whole_config(client):
    tid = seeded_demo(client)
    bad = client.post("/api/v1/networks", json={"name": "Guest", "kind": "guest",
                                                "subnet_prefix": "192.168", "expected_gateway": "192.168.5.1"})
    assert bad.status_code == 422
    ok = client.post("/api/v1/networks", json={"name": "Guest Wi-Fi", "kind": "guest",
                                               "subnet_prefix": "192.168.5", "expected_gateway": "192.168.5.1"})
    assert ok.status_code == 200 and ok.json()["slug"] == "guest-wi-fi"
    db = SessionLocal()
    e = db.query(m.Edit).filter_by(tenant_id=tid, path="/api/config").order_by(m.Edit.id.desc()).first()
    cfg = e.body["config"]
    assert [n["slug"] for n in cfg["networks"]][-1] == "guest-wi-fi"
    assert cfg["sites"] and cfg["thresholds"], "the /api/config edit must carry the WHOLE config"
    assert db.query(m.Audit).filter_by(action="network.create").count() == 1
    prim = next(n for n in client.get("/api/v1/networks").json()["networks"] if n["is_primary"])
    assert client.request("DELETE", f"/api/v1/networks/{prim['id']}").status_code == 422
    lab = next(n for n in client.get("/api/v1/networks").json()["networks"] if n["slug"] == "lab")
    assert client.request("DELETE", f"/api/v1/networks/{lab['id']}").status_code == 422   # has devices
    assert client.request("DELETE", f"/api/v1/networks/{lab['id']}?reassign={prim['id']}").status_code == 200
    assert db.query(m.Device).filter_by(network_id=lab["id"]).count() == 0


def test_crud_rejects_a_gateway_outside_the_prefix_and_a_bad_kind(client):
    seeded_demo(client)
    for body in (
        {"name": "Guest", "kind": "guest", "subnet_prefix": "192.168.5", "expected_gateway": "10.0.0.1"},
        {"name": "Guest", "kind": "satellite", "subnet_prefix": "192.168.5", "expected_gateway": "192.168.5.1"},
        {"name": "Lab", "kind": "lab", "subnet_prefix": "10.10.0", "expected_gateway": "10.10.0.1"},  # dup slug
    ):
        assert client.post("/api/v1/networks", json=body).status_code == 422, body


def test_patch_renames_and_keeps_is_primary_read_only(client):
    tid = seeded_demo(client)
    lab = next(n for n in client.get("/api/v1/networks").json()["networks"] if n["slug"] == "lab")
    r = client.patch(f"/api/v1/networks/{lab['id']}", json={"name": "Lab VLAN 40", "colour": "violet"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Lab VLAN 40" and r.json()["colour"] == "violet"
    # The slug is frozen at creation: it is the network segment of every LAN
    # alert key for this network, so re-slugging on rename would orphan them.
    assert r.json()["slug"] == "lab"
    assert r.json()["is_primary"] is False
    db = SessionLocal()
    assert db.query(m.Audit).filter_by(tenant_id=tid, action="network.update").count() == 1
    e = db.query(m.Edit).filter_by(tenant_id=tid, path="/api/config").order_by(m.Edit.id.desc()).first()
    entry = next(n for n in e.body["config"]["networks"] if n["slug"] == "lab")
    assert entry["name"] == "Lab VLAN 40"


def test_lan_alert_keys_are_namespaced_per_network(client):
    seeded_demo(client)
    db = SessionLocal()
    lan_keys = [a.key for a in db.query(m.Alert).filter(m.Alert.key.like("lan:%")).all()]
    assert lan_keys, "the fixture has LAN alerts"
    for key in lan_keys:
        assert key.startswith("lan:office-wi-fi:"), key
        # …and the parsers can still read the segments behind the namespace.
        assert networks.canonical_lan_key(key).startswith("lan:")
        assert not networks.canonical_lan_key(key).startswith("lan:office-wi-fi:")
    # An ack that only knows the legacy shape still lands on the right row.
    legacy = networks.canonical_lan_key(lan_keys[0])
    assert networks.find_alert(db, db.query(m.Alert).first().tenant_id, legacy) is not None


def test_device_edits_carry_the_network_slug(client):
    tid = seeded_demo(client)
    printer = demoscrub.map_mac("54:27:1e:d3:5c:fb")
    r = client.post(f"/api/v1/devices/{printer}", json={"label": "Lab printer", "trusted": True})
    assert r.status_code == 200, r.text
    assert r.json()["network"]["slug"] == "lab"
    db = SessionLocal()
    e = db.query(m.Edit).filter_by(tenant_id=tid, path="/api/device").order_by(m.Edit.id.desc()).first()
    assert e.body["net"] == "lab" and e.body["mac"] == printer


def test_security_carries_one_wifi_guard_per_network(client):
    seeded_demo(client)
    all_slugs = {n["slug"] for n in client.get("/api/v1/networks").json()["networks"]}
    body = client.get("/api/v1/security").json()
    # One guard row per `Network` the tenant has, including the demo's own
    # recorder's camera network (Task 83) -- it is unswept, not absent.
    assert len(body["wifis"]) == len(all_slugs)
    assert {g["network"]["slug"] for g in body["wifis"]} == all_slugs
    assert {"office-wi-fi", "lab"} <= all_slugs
    # `wifi` stays the primary's guard so every pre-Task-51 caller keeps working
    assert body["wifi"]["ssid"] == demoscrub.SSID
    # 3, not 2: the demo's own recorder (Task 83) owns a third, unswept camera network
    assert len(body["networks"]) == 3
    lab = next(n for n in client.get("/api/v1/networks").json()["networks"] if n["slug"] == "lab")
    scoped = client.get(f"/api/v1/security?network={lab['id']}").json()
    assert scoped["wifi"]["network"]["slug"] == "lab"


def test_slugify_and_ip_attribution():
    assert networks.slugify("Office Wi-Fi") == "office-wi-fi"
    assert networks.slugify("Lab / VLAN 40!") == "lab-vlan-40"
    assert len(networks.slugify("a" * 60)) == 24


def test_a_slug_can_never_be_a_lan_subkey(client):
    """A network called "New" would slug to `new`, and `lan:new:port:<mac>`
    would then be unstrippable in one direction and double-namespaced in the
    other -- so `slugify` reserves every LAN sub-key."""
    for reserved in networks.LAN_SUBKEYS:
        assert networks.slugify(reserved) == f"{reserved}-net"
    seeded_demo(client)
    r = client.post("/api/v1/networks", json={"name": "New", "kind": "guest",
                                              "subnet_prefix": "192.168.9",
                                              "expected_gateway": "192.168.9.1"})
    assert r.status_code == 200 and r.json()["slug"] == "new-net"
    # …and the key helpers round-trip for it.
    key = "lan:port:aa:bb:cc:dd:ee:ff:445"
    once = networks.lan_alert_key(key, "new-net")
    assert once == "lan:new-net:port:aa:bb:cc:dd:ee:ff:445"
    assert networks.lan_alert_key(once, "new-net") == once
    assert networks.canonical_lan_key(once) == key


def test_for_client_ip_prefers_the_longest_prefix(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    assert networks.for_client_ip(db, tid, "10.10.0.159").slug == "lab"
    assert networks.for_client_ip(db, tid, "192.168.1.5").slug == "office-wi-fi"
    assert networks.for_client_ip(db, tid, "172.16.0.1") is None


# --------------------------------------------------------------------------
# Fix round 1: tenant isolation, the delete union, config-entry preservation
# --------------------------------------------------------------------------

def _other_tenant_network(tid_self: str):
    """A network belonging to a DIFFERENT tenant, plus a device on it."""
    db = SessionLocal()
    t = m.Tenant(slug="other", name="Other Home")
    db.add(t)
    db.flush()
    n = m.Network(tenant_id=t.id, name="Their Wi-Fi", slug="their-wi-fi", kind="wifi",
                  subnet_prefix="172.16.0", is_primary=True, status="active")
    db.add(n)
    db.flush()
    db.add(m.Device(tenant_id=t.id, network_id=n.id, mac="ff:ff:ff:00:00:01", ip="172.16.0.5"))
    db.commit()
    assert t.id != tid_self
    return t.id, n.id, n.slug


def test_another_tenants_network_is_invisible_on_every_route(client):
    """Criterion (2): a foreign network id or slug must 404 -- never widen a
    read to the whole org, and never let a write reach across tenants."""
    tid = seeded_demo(client)
    _other_tid, other_id, other_slug = _other_tenant_network(tid)

    for ref in (other_id, other_slug):
        for path in ("/api/v1/devices", "/api/v1/events", "/api/v1/dns", "/api/v1/topology",
                     "/api/v1/inbox?view=alerts&", "/api/v1/security"):
            sep = "" if path.endswith("&") else "?"
            r = client.get(f"{path}{sep}network={ref}")
            assert r.status_code == 404, (path, ref, r.status_code)

    assert client.patch(f"/api/v1/networks/{other_id}", json={"name": "Mine now"}).status_code == 404
    assert client.request("DELETE", f"/api/v1/networks/{other_id}").status_code == 404
    # …and a foreign network cannot be the ?reassign= target either. Fix round
    # 3: this is a 404, not a 422 -- a reassign target belonging to another
    # tenant is indistinguishable from one that does not exist at all, exactly
    # like every other `?network=` consumer's foreign-id/-slug lookup above.
    lab = next(n for n in client.get("/api/v1/networks").json()["networks"] if n["slug"] == "lab")
    r = client.request("DELETE", f"/api/v1/networks/{lab['id']}?reassign={other_id}")
    assert r.status_code == 404, r.text
    r = client.request("DELETE", f"/api/v1/networks/{lab['id']}?reassign={other_slug}")
    assert r.status_code == 404, r.text
    # A reassign target that IS this tenant's, but invalid for the operation
    # (the network being deleted, named by its slug), is still a 422.
    r = client.request("DELETE", f"/api/v1/networks/{lab['id']}?reassign={lab['slug']}")
    assert r.status_code == 422, r.text
    # Nothing moved, and the other tenant's own row is untouched.
    db = SessionLocal()
    assert db.query(m.Device).filter_by(network_id=other_id).count() == 1
    assert db.query(m.Network).filter_by(id=other_id).first() is not None
    assert db.query(m.Network).filter_by(tenant_id=tid, slug="lab").first() is not None


def test_reassign_target_resolves_by_slug(client):
    """Fix round 3: `?reassign=` accepts a slug, same as `networks.resolve()`
    (and every other `?network=` consumer), not only an id."""
    tid = seeded_demo(client)
    nets = client.get("/api/v1/networks").json()["networks"]
    lab = next(n for n in nets if n["slug"] == "lab")
    prim = next(n for n in nets if n["is_primary"])
    r = client.request("DELETE", f"/api/v1/networks/{lab['id']}?reassign={prim['slug']}")
    assert r.status_code == 200, r.text
    db = SessionLocal()
    assert db.query(m.Device).filter_by(network_id=lab["id"]).count() == 0
    assert db.query(m.Device).filter_by(tenant_id=tid, network_id=prim["id"]).count() > 0


def test_a_viewer_cannot_create_a_network(client):
    from tests.test_auth import seed_user, login
    seed_user(username="ann", email="a@x.com", tenant_slug="viewers", role="viewer")
    login(client, "ann", "a@x.com")
    r = client.post("/api/v1/networks", json={"name": "Guest Wi-Fi", "kind": "guest",
                                              "subnet_prefix": "192.168.5"})
    assert r.status_code == 403, r.text
    assert client.get("/api/v1/networks").status_code == 200      # reads are viewer+


def test_delete_reassigns_every_scoped_table_not_just_devices(client):
    """The Critical-2 regression: a network holding events/alerts/DNS/presence
    but NO devices used to be deleted outright, leaving those rows with a
    dangling `network_id` -- counted in no network's health and invisible to
    every `?network=` filter."""
    tid = seeded_demo(client)
    db = SessionLocal()
    prim = db.query(m.Network).filter_by(tenant_id=tid, is_primary=True).one()
    net = m.Network(tenant_id=tid, name="Guest Wi-Fi", slug="guest-wi-fi", kind="guest",
                    subnet_prefix="192.168.5", is_primary=False, status="no-sensor")
    db.add(net)
    db.flush()
    nid = net.id
    db.add_all([
        m.Event(tenant_id=tid, network_id=nid, ts="2026-09-09T00:00:00Z", type="lan", entity="e"),
        m.Alert(tenant_id=tid, network_id=nid, key="lan:guest-wi-fi:new:ee:ee:ee:00:00:01",
                sev="WARN", msg="m", why="w", first_seen="t0", last_seen="t1"),
        m.DnsQuery(tenant_id=tid, network_id=nid, ts="2026-09-09T00:00:00Z",
                   client="192.168.5.20", name="x.example", qtype="A", blocked=False, ms=3),
        m.PresenceSample(tenant_id=tid, network_id=nid, ts="2026-09-09T00:00:00Z",
                         mac="ee:ee:ee:00:00:01"),
    ])
    db.commit()
    assert db.query(m.Device).filter_by(network_id=nid).count() == 0

    # No devices, but four other tables hold rows -> still a 422 without a target.
    r = client.request("DELETE", f"/api/v1/networks/{nid}")
    assert r.status_code == 422 and "row(s) are still on this network" in r.json()["detail"]

    r = client.request("DELETE", f"/api/v1/networks/{nid}?reassign={prim.id}")
    assert r.status_code == 200, r.text
    assert r.json()["moved"] == {"device": 0, "event": 1, "alert": 1, "dns_query": 1,
                                 "presence_sample": 1}
    db = SessionLocal()
    for model in (m.Event, m.Alert, m.DnsQuery, m.PresenceSample):
        assert db.query(model).filter_by(network_id=nid).count() == 0, model.__name__
        assert db.query(model).filter_by(tenant_id=tid, network_id=prim.id).count() > 0
    assert db.query(m.Network).filter_by(id=nid).first() is None
    # The health strip still accounts for every open alert of the tenant.
    strip = client.get("/api/v1/overview").json()["networks"]
    assert sum(s["crit"] + s["warn"] for s in strip) == db.query(m.Alert).filter(
        m.Alert.tenant_id == tid, m.Alert.sev.in_(["CRIT", "WARN"]),
        m.Alert.status.notin_(["resolved", "snoozed"])).count()


def test_a_network_config_entry_keeps_keys_the_hub_does_not_own(client):
    """S3.1's per-entry `interface?` (Task 52) has no `Network` column. The
    whole-config Edit is replayed by the sensor with `cfg.clear()`, so an entry
    rebuilt from columns alone would drop it permanently."""
    tid = seeded_demo(client)
    db = SessionLocal()
    t = db.get(m.Tenant, tid)
    cfg = dict(t.config or {})
    # The stored entries carry only a name (what the sensor writes), so this
    # also pins that a stored entry is matched by `slugify(slug or name)`.
    cfg["networks"] = [dict(e) for e in cfg.get("networks") or []]
    assert cfg["networks"] and not any("slug" in e for e in cfg["networks"])
    for entry in cfg["networks"]:
        entry["interface"] = "eth-" + networks.slugify(entry["name"])
        entry["scan_every_s"] = 90
    t.config = cfg
    db.commit()

    lab = next(n for n in client.get("/api/v1/networks").json()["networks"] if n["slug"] == "lab")
    r = client.patch(f"/api/v1/networks/{lab['id']}", json={"colour": "violet"})
    assert r.status_code == 200, r.text
    db = SessionLocal()
    e = db.query(m.Edit).filter_by(tenant_id=tid, path="/api/config").order_by(m.Edit.id.desc()).first()
    entries = {n["slug"]: n for n in e.body["config"]["networks"]}
    assert entries["lab"]["interface"] == "eth-lab" and entries["lab"]["scan_every_s"] == 90
    assert entries["lab"]["colour"] == "violet", "hub-owned columns still win"
    assert entries["office-wi-fi"]["interface"] == "eth-office-wi-fi"
    # …and the stored config kept them too (the Edit and the row must agree).
    assert db.get(m.Tenant, tid).config["networks"][0]["interface"] == "eth-office-wi-fi"


def test_patch_does_not_demand_a_prefix_the_row_never_had(client):
    """A primary migrated by `0007_networks` from a config with no readable
    subnet has `subnet_prefix=""`; a rename must not 422 on it."""
    tid = seeded_demo(client)
    db = SessionLocal()
    net = db.query(m.Network).filter_by(tenant_id=tid, is_primary=True).one()
    net.subnet_prefix = ""
    db.commit()
    r = client.patch(f"/api/v1/networks/{net.id}", json={"name": "Head Office"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Head Office" and r.json()["subnet_prefix"] == ""
    # An explicitly supplied bad prefix is still rejected.
    assert client.patch(f"/api/v1/networks/{net.id}",
                        json={"subnet_prefix": "192.168"}).status_code == 422


def test_dns_clients_are_per_ip_and_network(client):
    """Private ranges overlap: the same client ip on two networks is two rows,
    each with its own `network` ref, not one row with an arbitrary one."""
    tid = seeded_demo(client)
    db = SessionLocal()
    nets = {n.slug: n for n in db.query(m.Network).filter_by(tenant_id=tid).all()}
    ts = db.query(m.DnsQuery).filter_by(tenant_id=tid).order_by(m.DnsQuery.id.desc()).first().ts
    ip = "192.168.1.77"
    for slug in ("office-wi-fi", "lab"):
        db.add(m.DnsQuery(tenant_id=tid, network_id=nets[slug].id, ts=ts, client=ip,
                          name="dup.example", qtype="A", blocked=False, ms=4))
    db.commit()
    rows = [c for c in client.get("/api/v1/dns").json()["clients"] if c["ip"] == ip]
    assert len(rows) == 2, rows
    assert {r["network"]["slug"] for r in rows} == {"office-wi-fi", "lab"}


def test_reassign_rewrites_lan_alert_keys_to_the_destination_slug(client):
    """Fix round 2: a bulk network_id move used to leave `alert.key` carrying
    the SOURCE slug, so the destination's next push opened a duplicate under
    the new slug while the moved row itself never closed."""
    tid = seeded_demo(client)
    db = SessionLocal()
    lab = db.query(m.Network).filter_by(tenant_id=tid, slug="lab").one()
    home = m.Network(tenant_id=tid, name="Home", slug="home", kind="wifi",
                     is_primary=False, status="active")
    db.add(home)
    db.flush()
    home_id = home.id
    db.add(m.Alert(tenant_id=tid, network_id=lab.id, key="lan:lab:new:aa:bb",
                   sev="WARN", msg="m", why="w", first_seen="t0", last_seen="t1"))
    db.commit()

    moved = networks.reassign(db, tid, lab.id, home_id)
    db.commit()
    assert moved["alert"] == 1
    db = SessionLocal()
    row = db.query(m.Alert).filter_by(tenant_id=tid, network_id=home_id).one()
    assert row.key == "lan:home:new:aa:bb"


def test_reassign_alert_clash_keeps_one_surviving_row(client):
    """The destination already holding the rewritten key (an open twin of the
    same finding) must not violate `UNIQUE(tenant_id, key)` -- the moved
    duplicate is dropped and the destination's row survives."""
    tid = seeded_demo(client)
    db = SessionLocal()
    lab = db.query(m.Network).filter_by(tenant_id=tid, slug="lab").one()
    home = m.Network(tenant_id=tid, name="Home", slug="home", kind="wifi",
                     is_primary=False, status="active")
    db.add(home)
    db.flush()
    home_id = home.id
    db.add_all([
        m.Alert(tenant_id=tid, network_id=lab.id, key="lan:lab:new:aa:bb",
                sev="WARN", msg="moving", why="w", first_seen="t0", last_seen="t1"),
        m.Alert(tenant_id=tid, network_id=home_id, key="lan:home:new:aa:bb",
                sev="WARN", msg="already there", why="w", first_seen="t0", last_seen="t1"),
    ])
    db.commit()

    moved = networks.reassign(db, tid, lab.id, home_id)
    db.commit()
    assert moved["alert"] == 0, moved
    # Fix round 3: the clash-dropped duplicate is counted, not silently lost.
    assert moved["dropped"]["alert"] == 1, moved
    db = SessionLocal()
    survivors = db.query(m.Alert).filter_by(tenant_id=tid, key="lan:home:new:aa:bb").all()
    assert len(survivors) == 1
    assert survivors[0].msg == "already there"


def test_delete_reports_and_audits_dropped_alert_count(client):
    """Fix round 3: `DELETE /networks/{id}?reassign=` surfaces
    `dropped["alert"]` (clash-deleted duplicates) in both the response and the
    audit body, alongside `moved`, rather than folding it invisibly into the
    plain per-table `moved` counts."""
    tid = seeded_demo(client)
    db = SessionLocal()
    lab = db.query(m.Network).filter_by(tenant_id=tid, slug="lab").one()
    prim = db.query(m.Network).filter_by(tenant_id=tid, is_primary=True).one()
    db.add_all([
        m.Alert(tenant_id=tid, network_id=lab.id, key="lan:lab:new:cc:dd",
                sev="WARN", msg="moving", why="w", first_seen="t0", last_seen="t1"),
        m.Alert(tenant_id=tid, network_id=prim.id, key=f"lan:{prim.slug}:new:cc:dd",
                sev="WARN", msg="already there", why="w", first_seen="t0", last_seen="t1"),
    ])
    db.commit()
    lab_id, prim_id = lab.id, prim.id

    r = client.request("DELETE", f"/api/v1/networks/{lab_id}?reassign={prim_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dropped"]["alert"] == 1, body
    # `moved["alert"]` keeps its plain per-table meaning -- rows that landed on
    # the destination -- and does NOT count the dropped duplicate.
    assert body["moved"]["alert"] == 0, body

    db = SessionLocal()
    e = db.query(m.Audit).filter_by(tenant_id=tid, action="network.delete").order_by(
        m.Audit.id.desc()).first()
    assert e.body["dropped"]["alert"] == 1, e.body
    assert e.body["moved"]["alert"] == 0, e.body


def test_network_health_carries_is_primary(client):
    """Round 2 of Task 51: `NetworkHealth.is_primary` lets a chip in the
    overview's per-network strip say which one is the attribution fallback,
    without a second lookup against `/api/v1/networks`."""
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    strip = networks.health(db, tenant)
    by_slug = {h["slug"]: h for h in strip}
    assert by_slug["office-wi-fi"]["is_primary"] is True
    assert by_slug["lab"]["is_primary"] is False


def test_a_lan_alert_op_reaches_the_sensor_in_the_key_shape_it_holds(client):
    """The owner's report: "when I press resolve now they are not working at
    all, it says done but I dont see anything being queued or done".

    A LAN alert lives here under its namespaced key because `store_scan`
    rewrites it on the way in, but the sensor keeps the legacy one in its own
    `alerts_db`. `sync.apply_edit` looks the replayed key up there and, on a
    miss, silently does nothing (`if x:`) -- so a dashboard resolve never
    reached the sensor, which went on reporting the alert open and had that
    written back over the row on the next scan.
    """
    tid = seeded_demo(client)
    db = SessionLocal()
    namespaced = db.query(m.Alert).filter(m.Alert.key.like("lan:%")).first().key
    assert namespaced.startswith("lan:office-wi-fi:")

    r = client.post(f"/api/v1/alerts/{namespaced}/resolve")
    assert r.status_code == 200, r.text

    db = SessionLocal()
    edit = (db.query(m.Edit).filter_by(tenant_id=tid, path="/api/alert")
              .order_by(m.Edit.id.desc()).first())
    assert edit is not None, "the resolve must be mirrored down to the sensor"
    # The key the sensor will look up in its own alerts_db, not ours.
    assert edit.body["key"] == networks.canonical_lan_key(namespaced)
    assert edit.body["key"] != namespaced, "the fixture key really is namespaced"
    assert edit.body["action"] == "resolve"
    # The row itself still carries the namespaced key it is stored under.
    assert db.query(m.Alert).filter_by(tenant_id=tid, key=namespaced).first().status == "resolved"


def test_a_non_lan_alert_op_keeps_its_key_untouched(client):
    """`canonical_lan_key` is a no-op off the `lan:` prefix, so every other
    alert still round-trips under exactly the key both sides already share."""
    tid = seeded_demo(client)
    db = SessionLocal()
    other = db.query(m.Alert).filter(~m.Alert.key.like("lan:%")).first()
    assert other is not None, "the fixture has non-LAN alerts"
    key = other.key

    assert client.post(f"/api/v1/alerts/{key}/ack").status_code == 200
    db = SessionLocal()
    edit = (db.query(m.Edit).filter_by(tenant_id=tid, path="/api/alert")
              .order_by(m.Edit.id.desc()).first())
    assert edit.body["key"] == key
