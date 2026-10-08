"""Task 73: cameras behind a recorder.

The shape of the problem being tested: the LAN sweep finds ONE camera-vendor
device (the NVR) in a house with a dozen cameras, because the cameras are on
the recorder's own PoE network. These tests cover registering the recorder,
what the sensor is handed, what happens to the answer, and -- the case that
matters most -- what the app says when the recorder's HTTP interface is off.
"""

import ast
import datetime as dt
import pathlib

from tests.test_seed_helpers import seeded_demo, enrol_as_tenant_admin
from guardian_hub.db import SessionLocal
from guardian_hub import models as m, recorders, remedy


def _nets(client):
    return {n["slug"]: n for n in client.get("/api/v1/networks").json()["networks"]}


def _register(client, **over):
    body = {"name": "Garage NVR", "vendor": "reolink", "host": "192.168.1.182",
            "port": 80, "scheme": "http", "username": "guardian", "password": "s3cret",
            "expected_channels": 12}
    body.update(over)
    return client.post("/api/v1/recorders", json=body)


def _channels(n, online=None):
    online = range(n) if online is None else online
    return [{"channel": i, "name": f"Cam {i + 1}", "online": i in online, "model": "RLC-810A"}
            for i in range(n)]


def _now() -> str:
    """A recorder push has to be the tenant's NEWEST scan: the Devices payload
    reads its live-mac set off the latest scan's raw payload, so a push dated
    before the seeded capture leaves every camera reading offline. This used
    to be a fixed instant that happened to be a day later than the (then also
    fixed) fixture -- newer purely by coincidence of capture dates.
    `tests.scanfixture` rebases the capture to the moment of import, which is
    always before any test body runs, so "now" is reliably newer."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _push(client, token, results, ts=None):
    payload = {"last": {"ts": ts or _now(), "recorders": results}, "state": {}}
    r = client.post("/api/v1/sensor/ingest", json=payload,
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()["stored"]


def _row(client, rid: str) -> dict:
    """The listing's own row for `rid`, by id -- never `["recorders"][0]`:
    the demo tenant seeds its own recorder (Task 83, "Clinic NVR", camera
    census fixture), so a test's freshly-registered recorder is never
    guaranteed to be the first row in the tenant's listing."""
    return next(r for r in client.get("/api/v1/recorders").json()["recorders"] if r["id"] == rid)


SENSOR_NVR = pathlib.Path(__file__).resolve().parents[2] / "sensor" / "guardian_sensor" / "nvr.py"


def _sensor_error_kinds() -> tuple:
    """`nvr.ERROR_KINDS`, read out of the sensor source rather than imported:
    the hub may not import `guardian_sensor` (it is installed on the sensor
    host), and the same contract as `test_remedy.py`'s fix-catalogue parse."""
    tree = ast.parse(SENSOR_NVR.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ERROR_KINDS":
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("ERROR_KINDS not found in sensor/guardian_sensor/nvr.py")


# --------------------------------------------------------------- registration
def test_registering_a_recorder_creates_its_own_camera_network(client):
    seeded_demo(client)
    r = _register(client)
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["status"] == "pending"
    assert row["has_credentials"] is True
    assert "password" not in row
    # its cameras get a network of their own, with the recorder as the gateway
    assert row["network_slug"]
    net = _nets(client)[row["network_slug"]]
    assert net["expected_gateway"] == "192.168.1.182"
    assert net["is_primary"] is False
    # ...and that network is NOT in the sensor's config: the sensor's LAN loop
    # is config-driven and must never try to ARP-sweep the recorder's PoE net.
    db = SessionLocal()
    cfg_slugs = {e.get("slug") for e in (db.query(m.Tenant).first().config or {}).get("networks") or []}
    assert row["network_slug"] not in cfg_slugs


def test_an_address_outside_our_own_networks_is_refused(client):
    seeded_demo(client)
    r = _register(client, host="8.8.8.8")
    assert r.status_code == 422
    assert "configured networks" in r.json()["detail"]
    assert _register(client, host="not-an-ip").status_code == 422


def test_the_same_recorder_cannot_be_registered_twice(client):
    seeded_demo(client)
    assert _register(client).status_code == 200
    dup = _register(client, name="Again")
    assert dup.status_code == 422 and "already registered" in dup.json()["detail"]


def test_validation_of_vendor_scheme_port_and_count(client):
    seeded_demo(client)
    assert _register(client, vendor="acme").status_code == 422
    assert _register(client, scheme="ftp").status_code == 422
    assert _register(client, port=0).status_code == 422
    assert _register(client, expected_channels=-1).status_code == 422
    # a make with no adapter yet is accepted, and reported honestly
    r = _register(client, vendor="hikvision", host="192.168.1.183")
    assert r.status_code == 200 and r.json()["supported"] is False


def test_writes_are_audited_and_never_carry_the_password(client):
    seeded_demo(client)
    rid = _register(client).json()["id"]
    client.patch(f"/api/v1/recorders/{rid}", json={"password": "newpass", "name": "Renamed"})
    db = SessionLocal()
    rows = db.query(m.Audit).filter(m.Audit.action.like("recorder.%")).all()
    assert {r.action for r in rows} == {"recorder.create", "recorder.update"}
    for row in rows:
        assert row.user and row.role and row.tenant_id
        blob = repr(row.body)
        assert "s3cret" not in blob and "newpass" not in blob
    # the credential itself is encrypted at rest, not a plaintext column
    rec = db.query(m.Recorder).filter_by(id=rid).first()
    assert "newpass" not in (rec.secret_enc or "")
    assert recorders.password(rec) == "newpass"


def test_only_an_admin_may_register_a_recorder(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    viewer = m.User(username="rviewer", email="rviewer@guardian.invalid")
    db.add(viewer)
    db.flush()
    db.add(m.Membership(user_id=viewer.id, tenant_id=tid, role="viewer"))
    db.commit()
    client.post("/api/v1/auth/verify", json={"username": "rviewer", "code": "424242"})
    assert _register(client).status_code == 403
    assert client.get("/api/v1/recorders").status_code == 200


# ------------------------------------------------------- the sensor's payload
def test_the_sensor_is_the_only_caller_that_gets_the_credential(client):
    seeded_demo(client)
    rec = _register(client).json()
    token = enrol_as_tenant_admin(client)
    sensor_rows = client.get("/api/v1/sensor/recorders",
                             headers={"Authorization": f"Bearer {token}"}).json()["recorders"]
    # the demo tenant seeds its own recorder (Task 83's camera census
    # fixture), so this is one of possibly several -- find this test's own
    body = next(r for r in sensor_rows if r["id"] == rec["id"])
    assert body["password"] == "s3cret" and body["username"] == "guardian"
    assert body["net"]                          # the camera network's slug
    # the browser's own listing never carries it
    assert "password" not in _row(client, rec["id"])
    # and neither does an unauthenticated caller get anything
    assert client.get("/api/v1/sensor/recorders").status_code == 401


# ------------------------------------------------------------------ the answer
def test_twelve_cameras_behind_one_nvr_become_twelve_devices(client):
    """The whole point: an ARP sweep sees one Reolink, and after the recorder
    is read the site has twelve cameras."""
    seeded_demo(client)
    rec = _register(client).json()
    token = enrol_as_tenant_admin(client)
    # `last_seen` is a real timestamp, not the "t" placeholder the rows below
    # use: `_util.effective_presence` reads an ONLINE row whose last_seen it
    # cannot parse as UNKNOWN rather than online -- deliberately, so a stale
    # observation never presents as a current one. A real recorder push
    # carries a real instant, and this test asserts all twelve read as online,
    # which is only true of cameras actually seen just now.
    devices = {f"02:00:00:00:00:{i:02x}": {
        "ip": "", "name": f"Cam {i + 1}", "vendor": "Reolink", "kind": "camera",
        "presence": "ONLINE", "ports": {}, "first_seen": "t", "last_seen": _now(),
        "behind_recorder": rec["id"], "channel": i, "synthetic_mac": True}
        for i in range(12)}
    stored = _push(client, token, [{
        "id": rec["id"], "name": "Garage NVR", "net": rec["network_slug"], "ok": True,
        "channels": _channels(12), "reported_count": 12, "model": "RLN8-410",
        "firmware": "v3.0.0.0", "devices": devices,
    }])
    assert stored["recorders"] == 1

    row = _row(client, rec["id"])
    assert row["status"] == "ok" and row["channel_count"] == 12 and row["channels_online"] == 12
    assert row["model"] == "RLN8-410" and row["problem"] == ""

    # they are ordinary devices on the recorder's network -- no special casing
    net = _nets(client)[rec["network_slug"]]
    assert net["cameras"] == 12 and net["devices"] == 12 and net["status"] == "active"
    listing = client.get(f"/api/v1/devices?network={net['id']}").json()
    assert listing["total"] == 12
    assert listing["cameras"] == 12 and listing["cameras_online"] == 12
    # Assets groups them under Cameras with no special case -- they are just
    # camera-kind devices on a network.
    assert sum(len(g["devices"]) for g in listing["groups"]) == 12
    # ...and the existing surfaces read them without knowing they are special
    topo = client.get(f"/api/v1/topology?network={net['id']}").json()
    assert len(topo["nodes"]) >= 12
    strip = client.get("/api/v1/overview").json()["networks"]
    chip = next(c for c in strip if c["slug"] == rec["network_slug"])
    assert chip["cameras"] == 12 and chip["sensor_ok"] is True


def test_a_channel_that_goes_offline_or_vanishes_keeps_its_row(client):
    seeded_demo(client)
    rec = _register(client, expected_channels=3).json()
    token = enrol_as_tenant_admin(client)

    def dev(i, presence="ONLINE"):
        return {f"02:00:00:00:00:{i:02x}": {
            "ip": "", "name": f"Cam {i + 1}", "vendor": "Reolink", "kind": "camera",
            "presence": presence, "ports": {}, "first_seen": "t", "last_seen": "t",
            "behind_recorder": rec["id"], "channel": i}}

    base = {"id": rec["id"], "name": "Garage NVR", "net": rec["network_slug"], "ok": True}
    _push(client, token, [{**base, "channels": _channels(3),
                           "devices": {**dev(0), **dev(1), **dev(2)}}])
    net = _nets(client)[rec["network_slug"]]
    assert net["devices"] == 3

    # channel 2 stops being listed at all: the row survives, and goes OFFLINE
    _push(client, token, [{**base, "channels": _channels(3)[:2],
                           "devices": {**dev(0), **dev(1)}}])
    db = SessionLocal()
    rows = db.query(m.Device).filter_by(network_id=net["id"]).all()
    assert len(rows) == 3, "a dropped channel must never erase the camera's history"
    assert {r.presence for r in rows} == {"ONLINE", "OFFLINE"}
    gone = next(r for r in rows if r.presence == "OFFLINE")
    assert gone.name == "Cam 3"


def test_a_failed_probe_never_reads_as_every_camera_leaving(client):
    seeded_demo(client)
    rec = _register(client).json()
    token = enrol_as_tenant_admin(client)
    base = {"id": rec["id"], "name": "Garage NVR", "net": rec["network_slug"]}
    _push(client, token, [{**base, "ok": True, "channels": _channels(2), "devices": {
        "02:00:00:00:00:00": {"name": "Cam 1", "kind": "camera", "presence": "ONLINE"},
        "02:00:00:00:00:01": {"name": "Cam 2", "kind": "camera", "presence": "ONLINE"}}}])
    _push(client, token, [{**base, "ok": False, "error_kind": "unreachable",
                           "error": "no route to host", "channels": [], "devices": {}}])
    net = _nets(client)[rec["network_slug"]]
    assert net["devices"] == 2, "the rows stay; only the recorder is in error"
    assert net["status"] == "unreachable"
    row = _row(client, rec["id"])
    assert row["status"] == "error" and row["channel_count"] == 2   # last known list


# ------------------------------------------ the case the owner is actually in
def test_http_interface_off_says_so_and_names_the_setting(client):
    """His real recorder: only port 9000 answers. The app must not show a
    silent zero -- it must say the HTTP interface looks off, name the setting,
    and hand off to chat."""
    seeded_demo(client)
    rec = _register(client).json()
    token = enrol_as_tenant_admin(client)
    _push(client, token, [{
        "id": rec["id"], "name": "Garage NVR", "net": rec["network_slug"], "ok": False,
        "error_kind": "http_disabled", "error": "the HTTP port refused the connection",
        "channels": [], "devices": {},
    }])
    row = _row(client, rec["id"])
    assert row["status"] == "error" and row["error_kind"] == "http_disabled"
    assert "HTTP interface appears to be switched off" in row["problem"]
    assert "will not speak an undocumented protocol" in row["problem"]
    action = row["owner_action"]
    for phrase in ("Settings -> Network -> Advanced -> Port Settings", "HTTP port (80)", "HTTPS (443)"):
        assert phrase in action, action
    assert row["channel_count"] == 0
    assert row["problem"] and row["owner_action"], "a silent zero is the wrong answer"


def test_every_error_kind_has_copy_and_a_next_step(client):
    kinds = _sensor_error_kinds()
    assert set(kinds) == set(recorders.ERROR_COPY), "the sensor and the hub disagree on error kinds"
    for kind in kinds:
        problem, action = recorders.error_copy(kind)
        assert problem and action, kind
        assert problem != recorders.GENERIC_ERROR[0], f"{kind} falls through to the generic copy"


def test_every_nvr_finding_has_a_human_remedy(client):
    """Each `nvr:` key the sensor can raise must resolve to real copy naming
    what only the owner can do -- never the generic "no remediation" fallback."""
    seeded_demo(client)
    rec = _register(client).json()
    token = enrol_as_tenant_admin(client)
    rid = rec["id"]
    keys = [f"nvr:http-off:{rid}", f"nvr:auth:{rid}", f"nvr:down:{rid}", f"nvr:setup:{rid}",
            f"nvr:cam-offline:{rid}:3", f"nvr:cam-gone:{rid}:3", f"nvr:cam-new:{rid}:3",
            f"nvr:count:{rid}"]
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    for key in keys:
        alert = m.Alert(tenant_id=tenant.id, key=key, sev="WARN", msg="x")
        got = remedy.resolve(db, tenant, alert, "admin")
        assert got.kind == "human", key
        assert "no remediation of my own" not in got.summary, key
        assert got.need and got.opening, key
    off = remedy.resolve(db, tenant, m.Alert(tenant_id=tenant.id, key=f"nvr:http-off:{rid}",
                                             sev="WARN", msg="x"), "admin")
    assert "Port Settings" in off.need and "HTTP port (80)" in off.need
    # the chat hand-off's opening carries the diagnosis, not just the alert text
    assert "switched off" in off.opening


def test_findings_land_on_the_recorders_own_network(client):
    seeded_demo(client)
    rec = _register(client).json()
    token = enrol_as_tenant_admin(client)
    rid = rec["id"]
    ts = _now()
    payload = {"last": {"ts": ts, "recorders": [
        {"id": rid, "name": "Garage NVR", "net": rec["network_slug"], "ok": True,
         "channels": _channels(2), "devices": {}}]},
        "state": {"alerts_db": {
            f"nvr:cam-offline:{rid}:1": {"sev": "WARN", "msg": "Cam 2 has stopped answering",
                                        "state": "open", "count": 1, "net": rec["network_slug"]}}}}
    r = client.post("/api/v1/sensor/ingest", json=payload,
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    net = _nets(client)[rec["network_slug"]]
    db = SessionLocal()
    alert = db.query(m.Alert).filter_by(key=f"nvr:cam-offline:{rid}:1").first()
    assert alert is not None and alert.network_id == net["id"]


# ------------------------------------------------------------------ lifecycle
def test_changing_how_the_recorder_is_reached_resets_the_verdict(client):
    seeded_demo(client)
    rec = _register(client).json()
    token = enrol_as_tenant_admin(client)
    _push(client, token, [{"id": rec["id"], "net": rec["network_slug"], "ok": True,
                           "channels": _channels(2), "devices": {}}])
    assert _row(client, rec["id"])["status"] == "ok"
    r = client.patch(f"/api/v1/recorders/{rec['id']}", json={"host": "192.168.1.190"})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    # a rename alone does not
    client.patch(f"/api/v1/recorders/{rec['id']}", json={"name": "Front NVR"})
    row = _row(client, rec["id"])
    assert row["name"] == "Front NVR" and row["status"] == "pending"
    assert _nets(client)[row["network_slug"]]["name"] == "Front NVR cameras"


def test_clearing_the_password_is_possible_and_reported(client):
    seeded_demo(client)
    rid = _register(client).json()["id"]
    r = client.patch(f"/api/v1/recorders/{rid}", json={"password": ""})
    assert r.status_code == 200 and r.json()["has_credentials"] is False


def test_deleting_a_recorder_keeps_the_cameras_history(client):
    seeded_demo(client)
    rec = _register(client).json()
    token = enrol_as_tenant_admin(client)
    _push(client, token, [{"id": rec["id"], "net": rec["network_slug"], "ok": True,
                           "channels": _channels(1), "devices": {
                               "02:00:00:00:00:00": {"name": "Cam 1", "kind": "camera",
                                                     "presence": "ONLINE"}}}])
    assert client.delete(f"/api/v1/recorders/{rec['id']}").status_code == 200
    remaining = client.get("/api/v1/recorders").json()["recorders"]
    assert all(r["id"] != rec["id"] for r in remaining)
    net = _nets(client)[rec["network_slug"]]
    assert net["devices"] == 1, "the camera rows are history, not a cascade"


def test_a_disabled_recorder_is_still_listed_for_the_sensor_to_skip(client):
    seeded_demo(client)
    rec = _register(client, enabled=False).json()
    token = enrol_as_tenant_admin(client)
    sensor_rows = client.get("/api/v1/sensor/recorders",
                             headers={"Authorization": f"Bearer {token}"}).json()["recorders"]
    body = next(r for r in sensor_rows if r["id"] == rec["id"])
    assert body["enabled"] is False
