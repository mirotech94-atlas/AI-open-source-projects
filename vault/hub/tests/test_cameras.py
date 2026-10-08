"""Task 69 (hub side): the camera census reaches the Assets and Security
payloads, and every new LAN sub-key round-trips through the network namespace.

Built on a sensor ingest (`make_super` + `enrol`) rather than `seed_demo`:
these assertions are about what one pushed scan turns into, and the demo seed
costs minutes.
"""


from guardian_hub import models as m, networks
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import assets as assets_payload
from tests.test_sensor_api import enrol
from tests.test_tenants import make_super
from tests import scanfixture



class _Row:
    """The two attributes `kind_of` reads, plus `ports`."""

    def __init__(self, vendor="", name="", label="", ports=None, kind=""):
        self.vendor, self.name, self.label, self.kind = vendor, name, label, kind
        self.ports = ports or {}


# ---------------- classification ----------------

def test_kind_of_files_cameras_from_vendor_name_and_port():
    assert assets_payload.kind_of(_Row(vendor="Reolink Innovation Limited", name="weme")) == "camera"
    assert assets_payload.kind_of(_Row(vendor="?", ports={"554": ""})) == "camera"
    assert assets_payload.kind_of(_Row(vendor="?", ports={"37777": ""})) == "camera"
    assert assets_payload.kind_of(_Row(vendor="?", name="nvr01")) == "camera"
    assert assets_payload.kind_of(_Row(vendor="Hangzhou Hikvision", ports={"80": ""})) == "camera"
    # an explicit kind from the sensor's own classifier still wins
    assert assets_payload.kind_of(_Row(vendor="?", kind="camera")) == "camera"


def test_kind_of_does_not_turn_the_router_the_printer_or_a_dev_box_into_a_camera():
    assert assets_payload.kind_of(_Row(vendor="WNC Corporation", label="Router (Fios CR1000A)",
                                        ports={"53": "", "80": "", "443": ""})) == "router"
    assert assets_payload.kind_of(_Row(vendor="Canon", label="Canon printer", ports={"9100": ""})) == "printer"
    # 8000 alone is a development server far more often than a camera
    assert assets_payload.kind_of(_Row(vendor="Intel Corporate", name="devbox", ports={"8000": ""})) != "camera"
    assert assets_payload.kind_of(_Row(vendor="Intel Corporate", name="webcam-host")) != "camera"


def test_a_camera_from_a_camera_maker_beats_the_router_rule():
    """A Reolink NVR serving a web admin page used to be filed as network gear,
    because the router text test ran before the camera one."""
    assert assets_payload.kind_of(
        _Row(vendor="Reolink Innovation Limited", name="nvr-gateway", ports={"80": "", "554": ""})) == "camera"


# ---------------- payloads ----------------

def _ingest(client):
    tid = make_super(client)
    tok = enrol(client, tid)
    last = scanfixture.load_last()
    state = scanfixture.load_state()
    r = client.post("/api/v1/sensor/ingest",
                    json={"last": last, "state": state, "events": [], "history": [],
                          "fixes": [], "dns_queries": []},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    return tid


def test_the_devices_payload_counts_cameras_per_network_and_overall(client):
    _ingest(client)
    body = client.get("/api/v1/devices").json()
    cams = [d for g in body["groups"] if g["group"] == "Cameras" for d in g["devices"]]
    assert cams, "the fixture's Reolink must land in the Cameras group"
    assert body["cameras"] == len(cams) >= 1
    assert body["cameras_online"] <= body["cameras"]
    # the per-network chip agrees with the cards
    assert sum(n["cameras"] for n in body["networks"]) == body["cameras"]
    chip = next(c for c in body["chips"] if c["group"] == "Cameras")
    assert chip["count"] == body["cameras"]


def test_the_sensors_own_camera_reasons_reach_the_device_card(client):
    """The one path the drawer depends on end to end:
    `known_macs[mac]["camera"]["reasons"]` -> `Device.extra["camera"]` ->
    `DeviceCard.camera_reasons`."""
    tid = make_super(client)
    tok = enrol(client, tid)
    last = scanfixture.load_last()
    state = scanfixture.load_state()
    mac = "ec:71:db:05:5a:aa"
    state["lan"]["known_macs"][mac].update(
        kind="camera",
        camera={"confidence": "high", "reasons": ["camera vendor (Reolink Innovation Limited)",
                                                  "answers on 554 (rtsp)"]})
    r = client.post("/api/v1/sensor/ingest",
                    json={"last": last, "state": state, "events": [], "history": [],
                          "fixes": [], "dns_queries": []},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    card = next(d for g in client.get("/api/v1/devices").json()["groups"]
                for d in g["devices"] if d["mac"] == mac)
    assert card["kind"] == "camera"
    assert "answers on 554 (rtsp)" in card["camera_reasons"]
    detail = client.get(f"/api/v1/devices/{mac}").json()
    assert "answers on 554 (rtsp)" in detail["device"]["camera_reasons"]


def test_the_security_payload_reports_the_camera_checks(client):
    _ingest(client)
    body = client.get("/api/v1/security").json()
    rows = {c["key"]: c for c in body["checks"] if c["area"] == "Cameras"}
    assert set(rows) == {"cameras:internet", "cameras:answering"}
    assert rows["cameras:internet"]["passed"] is True          # nothing exposed in the fixture
    assert all(r["evidence"] for r in rows.values())
    tile = next(t for t in body["exposure"] if t["label"] == "Cameras")
    assert "answering" in tile["value"]


def test_an_internet_facing_camera_port_fails_the_camera_check(client):
    _ingest(client)
    db = SessionLocal()
    try:
        scan = db.query(m.Scan).order_by(m.Scan.ts.desc()).first()
        raw = dict(scan.raw or {})
        home = dict(raw.get("home") or {})
        home["exposure"] = {**(home.get("exposure") or {}), "open_ports": [554], "wan_ip": "198.51.100.105"}
        scan.raw = {**raw, "home": home}
        db.commit()
    finally:
        db.close()
    body = client.get("/api/v1/security").json()
    row = next(c for c in body["checks"] if c["key"] == "cameras:internet")
    assert row["passed"] is False and "554" in row["evidence"]
    tile = next(t for t in body["exposure"] if t["label"] == "Cameras")
    assert tile["state"] == "crit" and "554" in tile["detail"]


def test_a_pinned_public_address_becomes_a_tile(client):
    tid = _ingest(client)
    db = SessionLocal()
    try:
        t = db.query(m.Tenant).filter_by(id=tid).first()
        t.config = {**(t.config or {}), "networks": [
            {"name": "Home network", "subnet_prefix": "192.168.1",
             "expected_gateway": "192.168.1.1", "expected_wan_ip": "198.51.100.105"}]}
        db.commit()
    finally:
        db.close()
    tiles = {t_["label"]: t_ for t_ in client.get("/api/v1/security").json()["exposure"]}
    row = tiles["Home network public address"]
    assert row["state"] == "ok" and row["value"] == "198.51.100.105"

    db = SessionLocal()
    try:
        t = db.query(m.Tenant).filter_by(id=tid).first()
        nets = [dict(n) for n in t.config["networks"]]
        nets[0]["expected_wan_ip"] = "203.0.113.9"
        t.config = {**t.config, "networks": nets}
        db.commit()
    finally:
        db.close()
    row = next(x for x in client.get("/api/v1/security").json()["exposure"]
               if x["label"] == "Home network public address")
    assert row["state"] == "crit" and "203.0.113.9" in row["detail"]


def test_the_wan_pin_survives_a_hub_side_network_write(client):
    """`expected_wan_ip` is sensor-owned: `to_config_entries` must carry it
    through untouched, or the first network edit in the admin UI would silently
    unpin every network."""
    tid = _ingest(client)
    db = SessionLocal()
    try:
        cfg = {"networks": [{"name": "Home network", "slug": "home-network",
                             "expected_wan_ip": "198.51.100.105", "interface": "en0"}]}
        entries = networks.to_config_entries(db, tid, cfg)
        assert entries and entries[0]["expected_wan_ip"] == "198.51.100.105"
        assert entries[0]["interface"] == "en0"
    finally:
        db.close()


# ---------------- key namespacing ----------------

def test_every_new_lan_sub_key_round_trips():
    for sub in ("camera-new", "camera-gone", "offsubnet", "second-router"):
        assert sub in networks.LAN_SUBKEYS
        legacy = f"lan:{sub}:aa:bb:cc:dd:ee:ff"
        namespaced = networks.lan_alert_key(legacy, "lab")
        assert namespaced == f"lan:lab:{sub}:aa:bb:cc:dd:ee:ff"
        assert networks.canonical_lan_key(namespaced) == legacy
        # idempotent, and a network named after a sub-key can never claim it
        assert networks.lan_alert_key(namespaced, "lab") == namespaced
        assert networks.slugify(sub) == f"{sub}-net"[:networks.SLUG_MAX].rstrip("-")


def test_a_camera_alert_lands_on_its_network(client):
    tid = make_super(client)
    tok = enrol(client, tid)
    last = scanfixture.load_last()
    state = scanfixture.load_state()
    mac = "ec:71:db:05:5a:aa"
    state["alerts_db"][f"lan:camera-gone:{mac}"] = {
        "key": f"lan:camera-gone:{mac}", "sev": "WARN",
        "msg": f"Camera Reolink camera (192.168.1.182) has stopped answering",
        "first_seen": last["ts"], "last_seen": last["ts"], "count": 1, "state": "open", "fires": 1,
    }
    r = client.post("/api/v1/sensor/ingest",
                    json={"last": last, "state": state, "events": [], "history": [],
                          "fixes": [], "dns_queries": []},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        prim = networks.primary(db, tid)
        row = networks.find_alert(db, tid, f"lan:camera-gone:{mac}")
        assert row is not None and row.network_id == prim.id
        assert row.key == networks.lan_alert_key(f"lan:camera-gone:{mac}", prim.slug)
    finally:
        db.close()
