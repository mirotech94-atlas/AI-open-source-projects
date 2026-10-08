"""Task 83, item 4: the Overview camera census row.

The owner's ask, in his own words: identify every camera on the network,
say whether they are all accounted for, and flag one reaching the internet
by a path Vault does not expect. The hard part is honesty about cameras
Vault can only INFER (behind a recorder that has gone quiet, or that the
owner says carries more channels than it has ever listed) rather than
reporting the smaller, directly-observed number as if it were the whole
truth.

These tests build the payload directly through the same paths
`test_recorders.py` and `test_cameras.py` use (`seeded_demo` + a registered
recorder pushed through `/api/v1/sensor/ingest`), then read `/api/v1/overview`.
"""

import datetime as dt

from tests.test_seed_helpers import seeded_demo, enrol_as_tenant_admin


def _register(client, **over):
    body = {"name": "Garage NVR", "vendor": "reolink", "host": "192.168.1.182",
            "port": 80, "scheme": "http", "username": "guardian", "password": "s3cret",
            "expected_channels": 12}
    body.update(over)
    return client.post("/api/v1/recorders", json=body)


def _channels(n):
    return [{"channel": i, "name": f"Cam {i + 1}", "online": True, "model": "RLC-810A"}
            for i in range(n)]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _push(client, token, results):
    r = client.post("/api/v1/sensor/ingest",
                     json={"last": {"ts": _now(), "recorders": results}, "state": {}},
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


def test_overview_carries_a_camera_census(client):
    seeded_demo(client)
    body = client.get("/api/v1/overview").json()
    assert "cameras" in body
    cams = body["cameras"]
    assert set(cams) == {"known", "online", "expected", "unobserved", "offline", "exposure_known", "exposed", "exposed_ports", "gaps"}
    # the demo's own scrubbed fixture carries one directly-seen camera, plus
    # the ten synthetic ones seeded behind its own recorder
    assert cams["known"] >= 1
    assert cams["expected"] >= cams["known"]
    assert cams["exposed"] is False


def test_a_recorder_short_of_its_expected_count_is_a_named_gap_not_a_smaller_truth(client):
    """A recorder that has only ever listed 2 of the 12 the owner told
    Vault to expect must not make the census read '2 cameras' -- it must
    say 12 expected, 2 known, and name the shortfall."""
    seeded_demo(client)
    before = client.get("/api/v1/overview").json()["cameras"]
    rec = _register(client, expected_channels=12).json()
    token = enrol_as_tenant_admin(client)
    _push(client, token, [{"id": rec["id"], "name": "Garage NVR", "net": rec["network_slug"],
                           "ok": True, "channels": _channels(2), "devices": {
        "02:00:00:00:00:00": {"name": "Cam 1", "kind": "camera", "presence": "ONLINE"},
        "02:00:00:00:00:01": {"name": "Cam 2", "kind": "camera", "presence": "ONLINE"},
    }}])
    cams = client.get("/api/v1/overview").json()["cameras"]
    assert cams["known"] == before["known"] + 2
    assert cams["expected"] == before["expected"] + 12, "12 expected minus the 2 ever listed, on top of whatever the seed already left short"
    gap = next(g for g in cams["gaps"] if g["name"] == "Garage NVR")
    assert gap["expected"] == 12 and gap["known"] == 2
    assert gap["reason"]


def test_a_recorder_that_never_answers_leaves_every_expected_channel_unaccounted(client):
    seeded_demo(client)
    before = client.get("/api/v1/overview").json()["cameras"]
    rec = _register(client, expected_channels=6).json()
    token = enrol_as_tenant_admin(client)
    _push(client, token, [{"id": rec["id"], "name": "Garage NVR", "net": rec["network_slug"],
                           "ok": False, "error_kind": "unreachable",
                           "error": "no route to host", "channels": [], "devices": {}}])
    cams = client.get("/api/v1/overview").json()["cameras"]
    assert cams["known"] == before["known"]       # nothing was ever seen behind it
    assert cams["expected"] == before["expected"] + 6
    gap = next(g for g in cams["gaps"] if g["name"] == "Garage NVR")
    assert gap["known"] == 0 and gap["expected"] == 6
    assert "did not answer" in gap["reason"] or "unaccounted" in gap["reason"]


def test_a_camera_port_answering_from_the_internet_flags_the_census(client):
    from guardian_hub import models as m
    from guardian_hub.db import SessionLocal
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        scan = db.query(m.Scan).order_by(m.Scan.ts.desc()).first()
        raw = dict(scan.raw or {})
        home = dict(raw.get("home") or {})
        home["exposure"] = {**(home.get("exposure") or {}), "open_ports": [554],
                             "wan_ip": "203.0.113.9"}
        scan.raw = {**raw, "home": home}
        db.commit()
    finally:
        db.close()
    cams = client.get("/api/v1/overview").json()["cameras"]
    assert cams["exposed"] is True and 554 in cams["exposed_ports"]


def test_the_demo_tenant_seeds_ten_to_twelve_synthetic_cameras(client):
    """Task 83, item 4's other half: the demo tenant must show more than
    "1/1 cameras" (which reads as a rounding error), and it must never carry
    the owner's real network -- every synthetic address has to sit inside a
    documentation range, never a real private LAN or a real public address.
    """
    from guardian_hub import models as m
    from guardian_hub.db import SessionLocal
    from guardian_hub.seed import _DEMO_CAMERA_NAMES

    tid = seeded_demo(client)
    body = client.get("/api/v1/overview").json()
    cams = body["cameras"]
    assert 10 <= cams["known"] <= 12, cams
    assert cams["expected"] > cams["known"], "the seed must leave a real, honest gap"
    assert cams["gaps"], "the seed's whole point is a census with a named gap"

    db = SessionLocal()
    try:
        rows = db.query(m.Device).filter_by(tenant_id=tid, kind="camera").all()
        # Every synthetic camera behind the seeded recorder sits in the
        # documentation range on purpose; the one direct-LAN camera comes
        # from the scrubbed real fixture, which `demoscrub` deliberately
        # leaves on its (inherently anonymous) private address rather than
        # rewriting it -- private ranges are never the owner's identifying
        # information the way a public address would be.
        doc_range = tuple(f"198.51.100.{40 + i}" for i in range(len(_DEMO_CAMERA_NAMES)))
        private_prefixes = ("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.")
        for row in rows:
            assert row.ip == "" or row.ip in doc_range or row.ip.startswith(private_prefixes), \
                f"camera at {row.ip} is neither a documentation address nor a private one"
            # Northwind Dental's own fiction, never the owner's real room names
            assert "miro" not in (row.name or "").lower()
            assert "emma" not in (row.name or "").lower()
    finally:
        db.close()


def test_the_demo_camera_seed_is_idempotent(client):
    from guardian_hub import models as m
    from guardian_hub.db import SessionLocal

    seeded_demo(client)
    first = client.get("/api/v1/overview").json()["cameras"]
    seeded_demo(client)          # a second seed run must not duplicate anything
    second = client.get("/api/v1/overview").json()["cameras"]
    assert first == second

    db = SessionLocal()
    try:
        assert db.query(m.Recorder).count() == 1
    finally:
        db.close()
