"""The site gateway router card: register the admin credential, hand it to the
enrolled sensor (and ONLY the sensor), enqueue a credential Test, and record
the result the sensor reports back.

The invariants under test are the same ones the recorder credential has:
the password goes in and never comes back out to a browser or the audit log,
and it reaches exactly one place -- the enrolled sensor -- over its own
authenticated channel. Nothing here mutates a router.
"""
from tests.test_seed_helpers import seeded_demo, enrol_as_tenant_admin
from guardian_hub.db import SessionLocal
from guardian_hub import models as m




def _sole_sensor():
    """Leave only the newest sensor active.

    `jobdispatch.eligible_sensor` routes a job to ONE sensor, preferring one
    whose latest scan proves it can reach the target. The demo seed already
    owns such a sensor, so a freshly enrolled test sensor never wins the
    router job and polls forever. These tests are about the router card, not
    about routing between two sensors.
    """
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    db = SessionLocal()
    try:
        rows = db.query(m.Sensor).order_by(m.Sensor.last_seen).all()
        for r in rows[:-1]:
            r.status = "retired"
        # The seed binds its networks to the sensor being retired here, and a
        # network bound to another sensor denies this one access, so the job
        # would wait for a sensor that can never come.
        for n in db.query(m.Network).all():
            n.sensor_id = rows[-1].id
        db.commit()
    finally:
        db.close()


def _claim(client, hdr, qid, tries=40):
    """Poll the sensor queue until `qid` is handed out.

    `jobdispatch.claim` deliberately returns at most ONE job per poll, so a
    sensor's execution clock does not run on a second job while it works on
    the first. A test that reads the queue once therefore sees whichever job
    was enqueued earliest, not necessarily its own.
    """
    for _ in range(tries):
        for q in client.get("/api/v1/sensor/queue", headers=hdr).json()["queued"]:
            if q["id"] == qid:
                return q
    raise AssertionError(f"job {qid} was never handed to the sensor")


def _save(client, **body):
    return client.put("/api/v1/router", json=body)


def test_unconfigured_card_is_empty_and_safe(client):
    seeded_demo(client)
    card = client.get("/api/v1/router").json()
    assert card["configured"] is False
    assert card["has_credentials"] is False
    assert card["vendor"] == "fios"
    assert "password" not in card and "secret_enc" not in card


def test_save_stores_credential_but_never_returns_it(client):
    seeded_demo(client)
    r = _save(client, base_url="https://192.168.1.1", password="Godisgood@x")
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["configured"] and card["has_credentials"]
    assert card["base_url"] == "https://192.168.1.1"
    # The password is nowhere in any browser response.
    assert "password" not in card
    body = client.get("/api/v1/router").json()
    assert "password" not in body and body["has_credentials"] is True


def test_public_base_url_is_refused(client):
    seeded_demo(client)
    r = _save(client, base_url="https://8.8.8.8", password="x")
    assert r.status_code == 422
    r2 = _save(client, base_url="https://router.example.com", password="x")
    assert r2.status_code == 422


def test_only_the_sensor_channel_carries_the_password(client):
    tid = seeded_demo(client)
    _save(client, base_url="https://192.168.1.1", username="", password="topsecret")
    token = enrol_as_tenant_admin(client)
    payload = client.get("/api/v1/sensor/router",
                         headers={"Authorization": f"Bearer {token}"}).json()
    assert payload["router"]["password"] == "topsecret"
    assert payload["router"]["base_url"] == "https://192.168.1.1"


def test_disabled_or_empty_router_hands_the_sensor_nothing(client):
    seeded_demo(client)
    token = enrol_as_tenant_admin(client)
    # nothing configured yet
    j = client.get("/api/v1/sensor/router",
                   headers={"Authorization": f"Bearer {token}"}).json()
    assert j["router"] is None
    _save(client, base_url="https://192.168.1.1", password="x", enabled=False)
    j2 = client.get("/api/v1/sensor/router",
                    headers={"Authorization": f"Bearer {token}"}).json()
    assert j2["router"] is None


def test_test_enqueues_a_credential_less_job_and_records_the_result(client):
    tid = seeded_demo(client)
    _save(client, base_url="https://192.168.1.1", password="x")
    token = enrol_as_tenant_admin(client)
    hdr = {"Authorization": f"Bearer {token}"}
    _sole_sensor()

    out = client.post("/api/v1/router/test")
    assert out.status_code == 200, out.text
    qid = out.json()["id"]

    # The queued job carries nothing sensitive -- the sensor fetches the
    # credential itself from GET /router at execution time.
    row = _claim(client, hdr, qid)
    assert row["path"] == "/api/router/test"
    assert row["body"] == {}

    # The audit row for the test must not carry a credential (body was {}).
    db = SessionLocal()
    try:
        aud = (db.query(m.Audit).filter_by(tenant_id=tid, action="router.test")
               .order_by(m.Audit.ts.desc()).first())
        assert aud is not None
        assert "password" not in (aud.body or {})
    finally:
        db.close()

    # Sensor reports success; the card records ok + a safe message.
    client.post(f"/api/v1/sensor/queue/{qid}/result",
                json={"ok": True, "out": "credential accepted", "result": None}, headers=hdr)
    card = client.get("/api/v1/router").json()
    assert card["last_test_ok"] is True
    assert card["status"] == "ok"
    assert "credential accepted" in card["last_test_msg"]


def test_test_does_not_strand_the_card_in_a_testing_state(client):
    # Regression: POST /test must NOT persist status="testing" on the row. If
    # no sensor picks the queued job up, a persisted "testing" would leave the
    # card disabled and self-polling forever with no way out. The status is a
    # last-known-result, so it stays whatever it was until a result lands.
    seeded_demo(client)
    _save(client, base_url="https://192.168.1.1", password="x")
    before = client.get("/api/v1/router").json()["status"]
    assert client.post("/api/v1/router/test").status_code == 200
    after = client.get("/api/v1/router").json()["status"]
    assert after != "testing"
    assert after == before  # unchanged until a real result arrives


def test_test_without_a_credential_is_refused(client):
    seeded_demo(client)
    r = client.post("/api/v1/router/test")
    assert r.status_code == 400


def _verify(client):
    """Save a router and mark its credential Test passed (a sensor result),
    so the disable action's precondition is met."""
    _save(client, base_url="https://192.168.1.1", password="x")
    token = enrol_as_tenant_admin(client)
    hdr = {"Authorization": f"Bearer {token}"}
    _sole_sensor()
    out = client.post("/api/v1/router/test").json()
    _claim(client, hdr, out["id"])
    r = client.post(f"/api/v1/sensor/queue/{out['id']}/result",
                    json={"ok": True, "out": "credential accepted", "result": None}, headers=hdr)
    assert r.status_code == 200, r.text
    return hdr


def test_disable_needs_a_passing_test_first(client):
    seeded_demo(client)
    _save(client, base_url="https://192.168.1.1", password="x")
    # Never tested -> confirm is refused, so the first live login is never a
    # mutating one.
    r = client.post("/api/v1/router/disable-remote-admin/confirm")
    assert r.status_code == 409


def test_disable_is_refused_without_a_confirmation(client):
    seeded_demo(client)
    _verify(client)
    r = client.post("/api/v1/router/disable-remote-admin", json={})
    assert r.status_code == 409  # must mint + send a token first


def test_disable_confirm_then_run_enqueues_a_credential_less_job(client):
    tid = seeded_demo(client)
    hdr = _verify(client)
    conf = client.post("/api/v1/router/disable-remote-admin/confirm")
    assert conf.status_code == 200, conf.text
    token = conf.json()["token"]
    assert conf.json()["tier"] == 3
    assert "remote administration" in conf.json()["summary"].lower()

    run = client.post("/api/v1/router/disable-remote-admin", json={"confirm_token": token})
    assert run.status_code == 200, run.text
    qid = run.json()["id"]
    row = _claim(client, hdr, qid)
    assert row["path"] == "/api/router/disable-remote-admin"
    assert row["body"] == {}

    db = SessionLocal()
    try:
        aud = (db.query(m.Audit).filter_by(tenant_id=tid, action="router.disable_remote_admin")
               .order_by(m.Audit.ts.desc()).first())
        assert aud is not None and "password" not in (aud.body or {})
    finally:
        db.close()


def test_disable_confirmation_is_single_use(client):
    seeded_demo(client)
    _verify(client)
    token = client.post("/api/v1/router/disable-remote-admin/confirm").json()["token"]
    assert client.post("/api/v1/router/disable-remote-admin", json={"confirm_token": token}).status_code == 200
    # Replaying the same token is refused with nothing queued.
    replay = client.post("/api/v1/router/disable-remote-admin", json={"confirm_token": token})
    assert replay.status_code == 403


def test_delete_clears_the_router(client):
    seeded_demo(client)
    _save(client, base_url="https://192.168.1.1", password="x")
    assert client.delete("/api/v1/router").status_code == 200
    assert client.get("/api/v1/router").json()["configured"] is False


# --------------------------------------------------------------------------
# Remembering a disable that already ran.
#
# The bug these pin: the owner pressed "Turn off remote administration", the
# sensor did it, the toast said so -- and the card offered the same action
# again, because nothing wrote the outcome down. The card's offer was gated on
# `last_test_ok` alone, so it re-nagged forever.
#
# What is recorded is the ACT, not the router's current state: the Fios config
# API is a write-only diff model with no getter, so the hub can never read the
# live value back. Every assertion below is about Vault's own record.
# --------------------------------------------------------------------------

def _run_disable(client, hdr, *, ok: bool, out: str = "remote administration disabled"):
    """Confirm + run the disable, then hand back the sensor's result."""
    token = client.post("/api/v1/router/disable-remote-admin/confirm").json()["token"]
    qid = client.post("/api/v1/router/disable-remote-admin",
                      json={"confirm_token": token}).json()["id"]
    _claim(client, hdr, qid)
    r = client.post(f"/api/v1/sensor/queue/{qid}/result",
                    json={"ok": ok, "out": out, "result": None}, headers=hdr)
    assert r.status_code == 200, r.text
    return client.get("/api/v1/router").json()


def test_a_fresh_card_has_no_disable_record(client):
    seeded_demo(client)
    _verify(client)
    # Empty means "Vault has never run this", NOT "remote admin is on".
    assert client.get("/api/v1/router").json()["remote_admin_off_at"] == ""


def test_a_successful_disable_is_remembered(client):
    seeded_demo(client)
    hdr = _verify(client)
    card = _run_disable(client, hdr, ok=True)
    assert card["remote_admin_off_at"] != ""
    assert "remote administration disabled" in card["remote_admin_msg"]


def test_a_failed_disable_records_the_attempt_and_claims_nothing(client):
    seeded_demo(client)
    hdr = _verify(client)
    card = _run_disable(client, hdr, ok=False, out="login failed (403)")
    # A failed attempt is evidence about the attempt. It must not look like
    # the setting was turned off.
    assert card["remote_admin_off_at"] == ""
    assert "403" in card["remote_admin_msg"]


def test_a_failed_attempt_after_a_good_one_keeps_the_record(client):
    seeded_demo(client)
    hdr = _verify(client)
    _run_disable(client, hdr, ok=True)
    first = client.get("/api/v1/router").json()["remote_admin_off_at"]
    card = _run_disable(client, hdr, ok=False, out="router unreachable")
    assert card["remote_admin_off_at"] == first  # unchanged


def test_replacing_the_password_keeps_the_record_but_a_new_address_clears_it(client):
    seeded_demo(client)
    hdr = _verify(client)
    _run_disable(client, hdr, ok=True)

    # A new credential on the SAME gateway does not switch remote
    # administration back on, so the record stands. (Clearing it here is what
    # would put the answered nag straight back on the card.)
    after_pw = _save(client, password="a-new-one").json()
    assert after_pw["remote_admin_off_at"] != ""

    # A different address is a different router, about which Vault has
    # done nothing.
    after_addr = _save(client, base_url="https://192.168.2.1").json()
    assert after_addr["remote_admin_off_at"] == ""
    assert after_addr["remote_admin_msg"] == ""


def test_the_new_fields_carry_no_credential(client):
    """The disable message comes from the sensor and is persisted and shown to
    a browser, so it is a new place a credential could escape. It must not."""
    seeded_demo(client)
    _save(client, base_url="https://192.168.1.1", password="Sup3rSecret-pw")
    token = enrol_as_tenant_admin(client)
    hdr = {"Authorization": f"Bearer {token}"}
    _sole_sensor()
    out = client.post("/api/v1/router/test").json()
    _claim(client, hdr, out["id"])
    r = client.post(f"/api/v1/sensor/queue/{out['id']}/result",
                    json={"ok": True, "out": "credential accepted", "result": None}, headers=hdr)
    assert r.status_code == 200, r.text
    card = _run_disable(client, hdr, ok=True)
    blob = repr(card)
    assert "Sup3rSecret-pw" not in blob
    assert "password" not in card and "secret_enc" not in blob
