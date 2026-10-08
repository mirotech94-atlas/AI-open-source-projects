
from tests.test_auth import login, seed_user
from tests.test_tenants import make_super
from tests import scanfixture



def enrol(client, tid):
    code = client.post(f"/api/v1/tenants/{tid}/enrol-code").json()["code"]
    r = client.post("/api/v1/sensor/enrol", json={"code": code, "name": "mac", "platform": "darwin", "version": "0.1.0"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_enrol_once(client):
    tid = make_super(client)
    code = client.post(f"/api/v1/tenants/{tid}/enrol-code").json()["code"]
    assert client.post("/api/v1/sensor/enrol", json={"code": code, "name": "a", "platform": "linux", "version": "1"}).status_code == 200
    assert client.post("/api/v1/sensor/enrol", json={"code": code, "name": "a", "platform": "linux", "version": "1"}).status_code == 404


def test_enrol_bad_code_404(client):
    make_super(client)
    r = client.post("/api/v1/sensor/enrol", json={"code": "NOPE0000", "name": "a", "platform": "linux", "version": "1"})
    assert r.status_code == 404


def test_ingest_and_queue(client):
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    payload = {"last": scanfixture.load_last(), "state": scanfixture.load_state(), "events": [], "history": [], "fixes": [], "dns_queries": []}
    r = client.post("/api/v1/sensor/ingest", json=payload, headers=H); assert r.status_code == 200, r.text
    assert r.json()["stored"]["devices"] >= 20
    assert client.post("/api/v1/sensor/ingest", json=payload, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/v1/sensor/queue", headers=H).json() == {"queued": []}
    assert client.get("/api/v1/sensor/edits", headers=H).json()["edits"] == []
    assert client.get("/api/v1/sensor/config", headers=H).status_code == 200


def test_ingest_no_auth_header_401(client):
    tid = make_super(client); enrol(client, tid)
    r = client.post("/api/v1/sensor/ingest", json={"last": {}, "state": {}, "events": [], "history": [], "fixes": [], "dns_queries": []})
    assert r.status_code == 401


def test_sensors_enrol_code_tenant_admin_ok(client):
    """Tenant admins (not just superadmins) can mint an enrol code for
    their own active tenant via the new /api/v1/sensors/enrol-code route."""
    seed_user(role="admin")
    login(client)
    r = client.post("/api/v1/sensors/enrol-code")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["code"]) == 8
    assert body["sensor_id"]
    assert body["expires"]


def test_sensors_enrol_code_operator_forbidden(client):
    seed_user(role="operator")
    login(client)
    r = client.post("/api/v1/sensors/enrol-code")
    assert r.status_code == 403


def test_sensors_enrol_code_works_with_sensor_enrol(client):
    """The code minted by the tenant-admin route works with the existing
    sensor-facing POST /api/v1/sensor/enrol, same as a superadmin-minted one."""
    seed_user(role="admin")
    login(client)
    code = client.post("/api/v1/sensors/enrol-code").json()["code"]
    r = client.post("/api/v1/sensor/enrol", json={"code": code, "name": "mac", "platform": "darwin", "version": "0.1.0"})
    assert r.status_code == 200, r.text
    assert r.json()["token"]


def test_enrol_code_with_network_id_binds_sensor_on_enrol(client):
    """Round 2 of Task 51: `?network_id=` on the enrol-code mint pins the
    sensor that redeems it onto that network, without the admin having to
    PATCH the network afterwards."""
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m

    seed_user(role="admin")
    login(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).first()
    net = m.Network(tenant_id=tenant.id, name="Lab", slug="lab", kind="lab", is_primary=False)
    db.add(net)
    db.commit()
    net_id = net.id

    r = client.post("/api/v1/sensors/enrol-code", json={"network_id": net_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["network_id"] == net_id
    code = body["code"]

    r = client.post("/api/v1/sensor/enrol", json={"code": code, "name": "labsensor",
                                                   "platform": "linux", "version": "1"})
    assert r.status_code == 200, r.text
    sid = r.json()["sensor_id"]

    db = SessionLocal()
    row = db.query(m.Network).filter_by(id=net_id).one()
    assert row.sensor_id == sid


def test_enrol_code_with_unknown_network_id_404s(client):
    seed_user(role="admin")
    login(client)
    r = client.post("/api/v1/sensors/enrol-code", json={"network_id": "nope-not-real"})
    assert r.status_code == 404


def test_enrol_code_with_other_tenants_network_id_404s(client):
    """`network_id` is validated tenant-scoped: a network belonging to a
    different tenant must 404, not silently bind across tenants."""
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m

    other_tid = make_super(client)
    db = SessionLocal()
    other_net = m.Network(tenant_id=other_tid, name="Other", slug="other", is_primary=True)
    db.add(other_net)
    db.commit()
    other_net_id = other_net.id

    seed_user(role="admin", username="user2", email="user2@x.com", tenant_slug="home2")
    login(client, "user2", "user2@x.com")
    r = client.post("/api/v1/sensors/enrol-code", json={"network_id": other_net_id})
    assert r.status_code == 404


# ---------------- stale "running" reaping (Task: sensor-queue-latency) ----------------

def _backdate_queue_row(qid, ts_ago_seconds, progress_at_ago_seconds=None):
    import datetime as dt
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m

    db = SessionLocal()
    try:
        q = db.get(m.Queue, qid)
        q.ts = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=ts_ago_seconds)
        q.started_at = q.ts
        q.heartbeat_at = q.ts
        if progress_at_ago_seconds is not None:
            at = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=progress_at_ago_seconds)).isoformat()
            q.result = {**(q.result or {}), "progress": {"done": 1, "total": 2, "line": "x", "at": at}}
        db.commit()
    finally:
        db.close()


def test_stale_running_item_is_marked_failed_on_next_queue_poll(client):
    from tests.test_seed_helpers import enrol_as_tenant_admin, seeded_demo

    seeded_demo(client)
    tok = enrol_as_tenant_admin(client)
    H = {"Authorization": f"Bearer {tok}"}
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    from guardian_hub import security
    with SessionLocal() as db:
        sensor = db.query(m.Sensor).filter_by(token_hash=security.hash_token(tok)).one()
        primary = db.query(m.Network).filter_by(tenant_id=sensor.tenant_id, is_primary=True).one()
        primary.sensor_id = sensor.id
        db.commit()
    qid = client.post("/api/v1/speedtest").json()["id"]
    assert client.get("/api/v1/sensor/queue", headers=H).json()["queued"]   # claimed -> now "running"

    # Backdate it well past STALE_RUNNING_TIMEOUT_S with no progress heartbeat
    # at all -- exactly the "sensor died mid-run" shape.
    _backdate_queue_row(qid, ts_ago_seconds=3600)

    # The tenant's sensor reconnects and polls again -- this GET is what
    # reaps the stale row (see sensor.py's `_reap_stale_running`).
    retried = client.get("/api/v1/sensor/queue", headers=H).json()["queued"]
    assert len(retried) == 1 and retried[0]["id"] != qid
    retry = client.get(f"/api/v1/queue/{retried[0]['id']}").json()
    assert retry["result"]["retry_of"] == qid

    row = client.get(f"/api/v1/queue/{qid}").json()
    assert row["status"] == "failed"
    assert "timed out" in row["result"]["out"]


def test_running_item_with_a_recent_progress_heartbeat_is_not_reaped(client):
    from tests.test_seed_helpers import enrol_as_tenant_admin, seeded_demo

    seeded_demo(client)
    tok = enrol_as_tenant_admin(client)
    H = {"Authorization": f"Bearer {tok}"}
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    from guardian_hub import security
    with SessionLocal() as db:
        sensor = db.query(m.Sensor).filter_by(token_hash=security.hash_token(tok)).one()
        primary = db.query(m.Network).filter_by(tenant_id=sensor.tenant_id, is_primary=True).one()
        primary.sensor_id = sensor.id
        db.commit()
    qid = client.post("/api/v1/speedtest").json()["id"]
    assert client.get("/api/v1/sensor/queue", headers=H).json()["queued"]

    # Old enqueue time, but a progress heartbeat just now -- a legitimately
    # long-running full_upgrade must NOT be reaped just because it started
    # more than STALE_RUNNING_TIMEOUT_S ago.
    _backdate_queue_row(qid, ts_ago_seconds=3600, progress_at_ago_seconds=5)

    assert client.get("/api/v1/sensor/queue", headers=H).json()["queued"] == []  # nothing NEW queued
    row = client.get(f"/api/v1/queue/{qid}").json()
    assert row["status"] == "running"
